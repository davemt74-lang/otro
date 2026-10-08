"""Stage pinned Playwright Chromium as an installer-owned external runtime."""
from pathlib import Path
import hashlib,json,os,subprocess,sys
root=Path('dist/tools/browser').resolve();root.mkdir(parents=True,exist_ok=True)
env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':str(root)}
subprocess.run([sys.executable,'-m','playwright','install','--no-shell','chromium'],env=env,check=True)
matches=list(root.rglob('chrome.exe'))
if len(matches)!=1: raise RuntimeError('Pinned Chromium executable was not staged')
target=matches[0]
with target.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
(root/'manifest.json').write_text(json.dumps({'format':'vp3.browser-runtime.v1','playwright':'1.58.0','executable':target.relative_to(root).as_posix(),'sha256':digest},indent=2))
print('Managed Chromium staged and checksum recorded')
