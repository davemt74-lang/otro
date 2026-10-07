"""Verify merged CI artifacts and publish one-file deploy packages."""
from pathlib import Path
import hashlib,json,marshal,struct,subprocess,sys,time,types,zipfile,zlib

def api(path):
    return json.loads(subprocess.check_output(["gh","api",path],text=True))
def digest(data):
    return hashlib.sha256(data).hexdigest()
def file_digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
    return h.hexdigest()
def blob_digest(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
def normalize(code):
    if not isinstance(code,types.CodeType):return code
    return code.replace(co_filename="",co_consts=tuple(normalize(c) for c in code.co_consts))
def entries(data):
    cookie=data.rfind(b"MEI\x0c\x0b\x0a\x0b\x0e");assert cookie>=0
    fmt="!8sIIII64s"
    magic,length,toc_offset,toc_size,pyver,pylib=struct.unpack_from(fmt,data,cookie)
    assert pyver==312,pyver
    base=cookie+struct.calcsize(fmt)-length
    toc=data[base+toc_offset:base+toc_offset+toc_size];result={};pos=0
    while pos<len(toc):
        size,offset,compressed,raw,flag,kind=struct.unpack_from("!iIIIBB",toc,pos)
        assert size>=18
        name=toc[pos+18:pos+size].rstrip(b"\0").decode().replace("\\","/")
        result[name]=(offset,compressed,raw,flag,kind);pos+=size
    assert pos==len(toc)
    def read(name):
        offset,compressed,raw,flag,kind=result[name]
        content=data[base+offset:base+offset+compressed]
        if flag:content=zlib.decompress(content)
        assert len(content)==raw,name
        return content
    return result,read

def main():
    kind,repo,sha,run_id,tag,filename=sys.argv[1:]
    assert kind in ("home","cloud")
    source=Path("verified-source")
    assert subprocess.check_output(["git","rev-parse","HEAD"],cwd=source,text=True).strip()==sha
    green=None
    for attempt in range(90):
        runs=api("/repos/"+repo+"/actions/runs?head_sha="+sha+"&per_page=100")["workflow_runs"]
        runs=[r for r in runs if r["event"]=="push" and r["head_branch"]=="main"]
        assert runs,"No merged-main workflow evidence"
        failed=[r for r in runs if r["status"]=="completed" and r["conclusion"] not in ("success","skipped","neutral")]
        assert not failed,[(r["name"],r["conclusion"]) for r in failed]
        if all(r["status"]=="completed" for r in runs):
            green=runs;break
        print("Waiting for merged-main checks:",sum(r["status"]=="completed" for r in runs),"/",len(runs),flush=True)
        time.sleep(20)
    assert green is not None,"Merged-main checks did not finish"
    run=api("/repos/"+repo+"/actions/runs/"+run_id)
    assert run["head_sha"]==sha and run["status"]=="completed" and run["conclusion"]=="success"
    expected="HomeServer-Windows" if kind=="home" else "software-production-deploy"
    artifacts=api("/repos/"+repo+"/actions/runs/"+run_id+"/artifacts")["artifacts"]
    artifact=next(a for a in artifacts if a["name"]==expected and not a["expired"])
    archive=Path("verified-artifact.zip")
    with archive.open("wb") as f:
        subprocess.run(["gh","api","/repos/"+repo+"/actions/artifacts/"+str(artifact["id"])+"/zip"],stdout=f,check=True)
    archive_hash=file_digest(archive)
    assert "sha256:"+archive_hash==artifact["digest"]
    tree=subprocess.check_output(["git","ls-tree","-r","--full-tree","HEAD"],cwd=source,text=True)
    files={line.split("\t",1)[1]:line.split("\t",1)[0].split()[2] for line in tree.splitlines()}
    out=Path("deploy-output");out.mkdir(exist_ok=True)
    output=out/filename
    report={"repository":repo,"source_commit":sha,"workflow_run_id":int(run_id),"successful_main_workflows":len(green),"official_artifact_id":artifact["id"],"official_artifact_sha256":archive_hash}
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        if kind=="cloud":
            release=json.loads(z.read("VP3_RELEASE.json"))
            assert release["commit"]==sha
            assert "config.php" not in z.namelist() and ".env" not in z.namelist()
            checked=0
            for item in z.infolist():
                if item.is_dir() or item.filename=="VP3_RELEASE.json":continue
                assert item.filename in files,item.filename
                assert blob_digest(z.read(item))==files[item.filename],item.filename
                checked+=1
            assert checked>=1555,checked
            output.write_bytes(archive.read_bytes())
            report["verified_source_files"]=checked
            report["zip_contents"]="Flat Cloud deployment files"
        else:
            assert set(z.namelist())=={"HomeServer.exe","HomeServerSetup.exe","SHA256SUMS.txt","RELEASE.json"}
            sums={line.split()[1]:line.split()[0] for line in z.read("SHA256SUMS.txt").decode("utf-8-sig").splitlines() if line.strip()}
            release=json.loads(z.read("RELEASE.json").decode("utf-8-sig"))
            assert release["version"]=="2.4" and release["current_schema_version"]==68
            for name in ("HomeServer.exe","HomeServerSetup.exe"):
                data=z.read(name)
                assert data[:2]==b"MZ" and data[struct.unpack_from("<I",data,0x3c)[0]:][:4]==b"PE\0\0"
                assert digest(data)==sums[name]==release["files"][name],name
            del data
            exe=z.read("HomeServer.exe")
            names,read=entries(exe);checked=0
            for path,expected_hash in files.items():
                if not path.startswith(("ui/","database/")):continue
                assert path in names,"Unpackaged data: "+path
                data=read(path)
                if blob_digest(data)!=expected_hash and Path(path).suffix.lower() in {".sql",".js",".html",".css",".json",".svg",".txt",".xml",".md",".webmanifest"}:
                    data=data.replace(b"\r\n",b"\n")
                assert blob_digest(data)==expected_hash,"Source mismatch: "+path
                checked+=1
            pyz_names=[name for name in names if name.lower().endswith(".pyz")]
            assert len(pyz_names)==1
            pyz=read(pyz_names[0]);assert pyz[:4]==b"PYZ\0"
            toc=dict(marshal.loads(pyz[struct.unpack_from("!I",pyz,8)[0]:]));modules=0
            for name,(module_kind,offset,length) in toc.items():
                if name!="app" and not name.startswith("app."):continue
                path=name.replace(".","/")+".py"
                if path not in files:path=name.replace(".","/")+"/__init__.py"
                assert path in files,name
                text=subprocess.check_output(["git","show","HEAD:"+path],cwd=source)
                packaged=marshal.loads(zlib.decompress(pyz[offset:offset+length]))
                assert normalize(packaged)==normalize(compile(text,path,"exec",dont_inherit=True,optimize=0)),"Compiled source mismatch: "+name
                modules+=1
            for module in ("app.services.remote_bridge","app.services.workspace_sync","app.workspace_sync_api","app.services.ambient_agent"):assert module in toc,module
            assert checked>=214 and modules>=273,(checked,modules)
            with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as deploy:
                info=zipfile.ZipInfo("HomeServer.exe",(2026,10,6,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                info.external_attr=0o644<<16;deploy.writestr(info,exe,compresslevel=6)
            with zipfile.ZipFile(output) as deploy:
                assert deploy.namelist()==["HomeServer.exe"] and deploy.testzip() is None
                assert digest(deploy.read("HomeServer.exe"))==sums["HomeServer.exe"]
            report.update(verified_data_files=checked,verified_compiled_modules=modules,exe_sha256=sums["HomeServer.exe"],zip_contents=["HomeServer.exe"])
    report["zip_sha256"]=file_digest(output);report["zip_bytes"]=output.stat().st_size
    check_file=out/("SHA256SUMS-"+sha[:8]+".txt")
    checks=report["zip_sha256"]+"  "+output.name+"\n"
    if kind=="home":checks+=report["exe_sha256"]+"  HomeServer.exe\n"
    check_file.write_text(checks,encoding="utf-8")
    verification=out/("Build-Verification-"+sha[:8]+".json")
    verification.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    notes=out/("Release-Notes-"+sha[:8]+".md")
    if kind=="home":
        text="# HomeServer automatic sync release\n\nHomeServer ZIP contains only HomeServer.exe. Runtime v2.4; schema 68.\n\nUpdate Cloud first. Stop the old process, extract this ZIP into a permanent folder, replace HomeServer.exe and launch it. Keep the existing data folder and pairing.\n\nPowerShell stop command:\n\n    Get-Process -Name HomeServer -ErrorAction SilentlyContinue | Stop-Process -Force\n\nCloud data synchronization is on by default. Use the Cloud data sidebar view to browse copies, see the last complete sync and retry. Cloud and HomeServer source records remain authoritative; replicas are read-only. Copied schedules do not execute again.\n\nFor Ambient speech, enable Ambient Agent and Proactive voice, select Assume present (no sensor needed) if you have no presence sensor, include Info for ordinary reminders, then Save & Test Voice. Confirm the installed local voice and speaker work on your computer.\n\nAgent Brain actions, awareness, health and maintenance display local timestamps. Sync covers 16 owner-scoped categories including contacts/CRM, products/orders, calendar, knowledge documents/folders, transcriptions, schedules and meetings. New completed transcriptions synchronize text by default; existing private transcripts remain private. Local raw audio, biometric speaker evidence, credentials, billing authority and execution queues are excluded. Native Cloud uploads are transferred with checksum verification; external URLs remain references. HomeServer outbound mirrors carry permitted text/metadata.\n\nCapacity is 64 MiB per category and 4 GiB per Cloud file. A failed transfer preserves the previous complete copy and reports the error. Real account data volume, storage/network setup and installed audio hardware remain deployment checks.\n\nRelease acceptance: 10/10 defined checks passed: timestamps; default worker lifecycle; category coverage; ownership; complete UTF-8 records; original-file integrity; permission/revocation races; transcription privacy; browser/speech controls; Windows packaged behavior and exact source/ZIP verification. This score covers the defined acceptance checks, not every possible code path or installed hardware.\n\nPRs: https://github.com/davemt74-lang/software/pull/544 (36 checks, 15 PR workflows); https://github.com/davemt74-lang/otro/pull/286 (32 checks, 19 PR workflows); https://github.com/davemt74-lang/otro/pull/287 (10 checks, six PR workflows).\n\nWindows and Linux tests prove workspace service failures preserve live relay ping. Packaged Windows tests verify startup, takeover, shutdown/restart, the outbound bridge, saved HTTPS sessions, recovery, restore and installer private-data preservation. Every grouped native test command must succeed.\n\n"
    else:
        text="# VP3 Cloud automatic sync release\n\nDeploy this flat ZIP with your usual Cloud update process. Preserve config.php, .env, private/ and uploads/. Deploy Cloud before the updated HomeServer.\n\nThe paired account's owner-scoped data is synchronized to HomeServer by default. Sixteen categories include profile, contacts, CRM, knowledge/folders, transcriptions, calendar/tasks, schedules, meetings, products, orders, agents/memory, chats, notifications, music, other personal data and artist workspace. Permitted HomeServer text and metadata are mirrored back in Cloud data. Replicas are read-only; source records remain authoritative. Credential stores, billing authority, execution queues, global records and other users' data are excluded.\n\nFull text and owned native upload originals are checked before atomic replacement. External URLs remain references. Capacity is 64 MiB per category and 4 GiB per file; errors preserve complete copies.\n\nCloud PR: https://github.com/davemt74-lang/software/pull/544. All 36 checks, 15 PR workflows and 14 merged-main workflows passed. Every packaged source file matches the merged commit.\n\n"
    text+="Source commit: "+sha+"\n\nOfficial build: https://github.com/"+repo+"/actions/runs/"+run_id+"\n\nVerification: "+json.dumps(report,indent=2,sort_keys=True)+"\n"
    notes.write_text(text,encoding="utf-8")
    print(json.dumps(report,sort_keys=True),flush=True)
    subprocess.run(["gh","release","create",tag,str(output),str(check_file),str(verification),str(notes),"--repo",repo,"--target",sha,"--title",("HomeServer" if kind=="home" else "VP3 Cloud")+" automatic sync "+sha[:8],"--notes-file",str(notes)],check=True)

if __name__=="__main__":main()
