/**
 * Opt-in local self enrollment planning. This pure adapter never opens a camera,
 * stores biometrics, starts tracking, creates a third-party contact or reports
 * remote readiness. The existing participant runtime owns those actions.
 */
export const AUTO_ENROLLMENT_MIN_QUALITY = 0.68;
export const AUTO_ENROLLMENT_MIN_GAP_MS = 1300;
export const AUTO_ENROLLMENT_TARGET = 3;

export function autoEnrollmentDecision(state, input) {
  const { consent = false, active = false, samples = [], lastCaptureAt = 0 } = state || {};
  if (!consent || !active) return { capture: false, reason: 'explicit-owner-consent-required' };
  if (samples.length >= AUTO_ENROLLMENT_TARGET) return { capture: false, reason: 'ready' };
  if (Number(input?.faceCount) !== 1) return { capture: false, reason: 'exactly-one-face-required' };
  const quality = Number(input?.face?.quality || 0);
  const embedding = input?.face?.embedding;
  if (quality < AUTO_ENROLLMENT_MIN_QUALITY || !embedding?.length) {
    return { capture: false, reason: 'move-closer-and-hold-steady' };
  }
  const now = Number(input.now || 0);
  if (lastCaptureAt && now - lastCaptureAt < AUTO_ENROLLMENT_MIN_GAP_MS) {
    return { capture: false, reason: 'hold-for-next-angle' };
  }
  const f = input.face;
  const yaw = Number(f?.rotation?.yaw ?? 0);
  const pitch = Number(f?.rotation?.pitch ?? 0);
  const box = f?.box || {};
  const pose = { yaw, pitch, cx: Number(box.cx || 0), cy: Number(box.cy || 0) };
  if (samples.length) {
    const novel = samples.every(s => {
      const prior = s.pose || {};
      return Math.abs(yaw - Number(prior.yaw || 0)) >= 8
        || Math.abs(pitch - Number(prior.pitch || 0)) >= 8
        || Math.hypot(pose.cx - Number(prior.cx || 0), pose.cy - Number(prior.cy || 0)) >= 0.08;
    });
    if (!novel) return { capture: false, reason: 'turn-slightly-for-distinct-angle' };
  }
  return { capture: true, reason: 'quality-and-distinct-pose-confirmed', pose };
}

/** Export semantic, non-biometric receipt only after durable local storage. */
export function autoEnrollmentReceipt(participant) {
  if (participant?.visualEnrollment?.scope !== 'owner-self' || !participant?.visualEnrollment?.consentedAt) return null;
  if (participant.recognitionEnabled === false || !Array.isArray(participant.embeddings)
      || participant.embeddings.length < AUTO_ENROLLMENT_TARGET) return null;
  return {
    participantId: String(participant.id),
    state: 'enrolled_locally',
    samples: participant.embeddings.length,
    scope: 'owner-self',
    tracking: 'not_automatically_enabled',
    cloudSync: 'not_enabled',
    contactCreation: 'requires_owner_approval'
  };
}
