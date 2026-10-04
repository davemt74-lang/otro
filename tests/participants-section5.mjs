import assert from 'node:assert/strict';
import {bestParticipantMatch,cosineSimilarity,faceQuality,assignTracks} from '../ui/tracky/src/participant-core.js';
import {evaluateOwnerSelfCheck} from '../ui/tracky/src/owner-self-check-core.js';
let cases=0;
function test(name,fn){fn();cases++;console.log('PASS '+name);}
const owner={id:'owner',recognitionEnabled:true,embeddings:[[1,0,0],[1,.01,0],[1,.02,0]],visualEnrollment:{scope:'owner-self',consentedAt:'2026-10-04T00:00:00Z'}};
test('current valid profile matches only as local similarity',()=>{const m=bestParticipantMatch([1,0,0],[owner]);assert.equal(m.matched,true);assert.equal(m.participant.id,'owner');});
test('ambiguous profiles cannot select a person',()=>{const m=bestParticipantMatch([1,0,0],[owner,{...owner,id:'other'}]);assert.equal(m.matched,false);assert.equal(m.ambiguous,true);});
test('revoked profile cannot match',()=>assert.equal(bestParticipantMatch([1,0,0],[{...owner,recognitionEnabled:false}]).matched,false));
test('malformed descriptor cannot be coerced into a match',()=>{for(const vector of [[NaN,0,0],[Infinity,0,0],['1',0,0],[0,0,0],[1,0]])assert.equal(bestParticipantMatch(vector,[owner]).matched,false);});
test('malformed reference samples do not satisfy enrollment sample minimum',()=>{assert.equal(bestParticipantMatch([1,0,0],[{...owner,embeddings:[[1,0,0],[1,NaN,0],[1]]}]).matched,false);});
test('large finite vectors do not overflow similarity',()=>assert.ok(cosineSimilarity([1e308,1e308],[1e308,1e308])>.999));
test('malformed box cannot bypass self-check quality',()=>{for(const box of [[NaN,0,200,200],[0,0,Infinity,200],[0,0,-1,1]])assert.equal(faceQuality({box,score:1},960,720),0);});
test('expired spatial track cannot reclaim old identity',()=>{const box={cx:.5,cy:.5};const result=assignTracks([{id:'old',cx:.5,cy:.5,lastSeenAt:0,participantId:'owner'}],[{box}],2000,{nextId:()=> 'new'});assert.equal(result[0].id,'new');assert.equal(result[0].participantId,null);});
test('spatial association alone resets identity evidence',()=>{const result=assignTracks([{id:'track',cx:.5,cy:.5,lastSeenAt:100,participantId:'owner',participantName:'Owner',embedding:[1,0,0]}],[{box:{cx:.5,cy:.5}}],200);assert.equal(result[0].id,'track');assert.equal(result[0].participantId,null);assert.equal(result[0].embedding,null);});
test('self-check remains unverified and denies removed profile',()=>{const face={score:.99,box:[360,200,240,240],embedding:[1,0,0]};const result=evaluateOwnerSelfCheck({consent:true,active:true,participant:owner,faces:[face],width:960,height:720});assert.equal(result.matched,true);assert.equal(result.independently_verified,false);assert.equal(evaluateOwnerSelfCheck({consent:true,active:true,participant:null,faces:[face]}).matched,false);});
console.log(`PARTICIPANTS_SECTION5=PASS (${cases} matcher/lifecycle cases)`);
