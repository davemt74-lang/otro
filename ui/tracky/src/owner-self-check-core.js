/* Section 1F3: one-frame optional LOCAL self-profile comparison.
 * Reuses Tracky's canonical embedding matcher; never grants access, asserts
 * identity to HomeServer, stores new samples or recognizes another person.
 */
import { bestParticipantMatch } from './participant-core.js';
import { AUTO_ENROLLMENT_MIN_QUALITY, AUTO_ENROLLMENT_TARGET } from './auto-enrollment-core.js';

const readyVector = vector => Array.isArray(vector) && vector.length >= 4 &&
    vector.length <= 4096 && vector.every(x=>Number.isFinite(x)) &&
    vector.some(x=>Math.abs(x)>0.000001);

export function evaluateOwnerSelfFrame({
  consent=false, report=null, owner=null, faces=[], quality=0
}={}) {
  const denied = reason=>({
    outcome:'not_verified', reason, matched:false,
    identity_verified:false, independent_liveness_verified:false,
    hardware_certified:false, cloud_upload:false, persistent_result:false
  });
  if(consent!==true)return denied('fresh_owner_consent_required');
  if(!report||report.phase!=='browser_reported'||report.consented!==true)
    return denied('current_owner_browser_report_required');
  if(!owner||owner.visualEnrollment?.scope!=='owner-self'||
     owner.recognitionEnabled===false||owner.id!==report.local_participant_id)
    return denied('matching_self_profile_required');
  if(!Array.isArray(owner.embeddings)||owner.embeddings.length<AUTO_ENROLLMENT_TARGET)
    return denied('three_local_samples_required');
  if(!Array.isArray(faces)||faces.length!==1)
    return denied('exactly_one_face_required');
  const face=faces[0];
  const candidate=face?.embedding;
  if(!readyVector(candidate)||owner.embeddings.some(ref=>!readyVector(ref)||ref.length!==candidate.length))
    return denied('usable_matching_descriptor_required');
  if(!Number.isFinite(quality)||quality<AUTO_ENROLLMENT_MIN_QUALITY)
    return denied('sufficient_frame_quality_required');
  // One local profile only. A candidate match is never authentication,
  // liveness, third-party recognition or enrollment certification.
  const match=bestParticipantMatch(candidate,[owner],0.68,0.04,AUTO_ENROLLMENT_TARGET);
  return {
    outcome: match.matched&&match.participant?.id===owner.id
      ? 'local_similarity_candidate' : 'no_robust_local_candidate',
    reason: match.matched?'owner_profile_similarity_indication_only':'insufficient_similarity',
    matched: Boolean(match.matched&&match.participant?.id===owner.id),
    identity_verified:false, independent_liveness_verified:false,
    hardware_certified:false, cloud_upload:false, persistent_result:false
  };
}
