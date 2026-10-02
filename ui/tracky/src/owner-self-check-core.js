/** A browser-only, owner-initiated comparison of one current face descriptor.
 *
 * This never establishes legal/physical identity or hardware certification.
 * The existing Tracky participant matcher remains the single recognition
 * algorithm. The result is ephemeral and never sent to HomeServer or Cloud.
 */
import {bestParticipantMatch, faceQuality} from './participant-core.js';

export const OWNER_SELF_CHECK_QUALITY = 0.68;
export const OWNER_SELF_CHECK_DURATION_MS = 12000;

export function evaluateOwnerSelfCheck({
  consent=false, active=false, participant=null, faces=[], width=1, height=1
}={}){
  if (!consent || !active) return {state:'approval_required', matched:false};
  if (!participant || participant.visualEnrollment?.scope!=='owner-self' ||
      participant.recognitionEnabled===false ||
      !Array.isArray(participant.embeddings) ||
      participant.embeddings.length<3) return {state:'profile_unavailable',matched:false};
  if (!Array.isArray(faces) || faces.length!==1)
    return {state:faces?.length>1?'multiple_faces':'one_face_required',matched:false};
  const face=faces[0];
  const embedding=Array.isArray(face?.embedding)?face.embedding:
    face?.embedding && typeof face.embedding.length==='number'
      ? Array.from(face.embedding):[];
  if (embedding.length<3 || embedding.some(n=>!Number.isFinite(n)))
    return {state:'descriptor_unavailable',matched:false};
  const quality=faceQuality(face,width,height);
  if (quality<OWNER_SELF_CHECK_QUALITY)
    return {state:'improve_lighting_or_center',matched:false};
  const result=bestParticipantMatch(embedding,[participant],undefined,undefined,3);
  return {state:result.matched?'local_similarity_only':'local_comparison_not_matched',
          matched:result.matched,
          independently_verified:false,
          hardware_certified:false};
}
