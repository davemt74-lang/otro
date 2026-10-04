export const MATCH_THRESHOLD = 0.58;
export const MAX_TRACK_DISTANCE = 0.22;

export function clamp(value, min = 0, max = 1) {
  return Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : min;
}

export function normalizeBox(box, frameWidth = 1, frameHeight = 1) {
  let x = 0;
  let y = 0;
  let width = 0;
  let height = 0;

  if (Array.isArray(box)) {
    [x, y, width, height] = box;
  } else if (box && typeof box === 'object') {
    x = Number(box.x ?? box.left ?? 0);
    y = Number(box.y ?? box.top ?? 0);
    width = Number(box.width ?? box.w ?? 0);
    height = Number(box.height ?? box.h ?? 0);
  }

  const normalized = {
    x: clamp(x / Math.max(1, frameWidth)),
    y: clamp(y / Math.max(1, frameHeight)),
    width: clamp(width / Math.max(1, frameWidth)),
    height: clamp(height / Math.max(1, frameHeight))
  };
  normalized.cx = clamp(normalized.x + normalized.width / 2);
  normalized.cy = clamp(normalized.y + normalized.height / 2);
  return normalized;
}

export function faceQuality(face, frameWidth = 1, frameHeight = 1) {
  const raw = Array.isArray(face?.box) ? face.box : [face?.box?.x ?? face?.box?.left, face?.box?.y ?? face?.box?.top, face?.box?.width ?? face?.box?.w, face?.box?.height ?? face?.box?.h];
  if (!Number.isFinite(frameWidth) || !Number.isFinite(frameHeight) || frameWidth <= 0 || frameHeight <= 0 ||
      raw.length !== 4 || raw.some(v => !Number.isFinite(v)) || raw[2] <= 0 || raw[3] <= 0) return 0;
  const box = normalizeBox(face?.box, frameWidth, frameHeight);
  const detection = clamp(Number(face?.score ?? face?.confidence ?? 0.5));
  const area = box.width * box.height;
  const size = clamp(area / 0.08);
  const centerDistance = Math.hypot(box.cx - 0.5, box.cy - 0.45);
  const centered = clamp(1 - centerDistance / 0.72);

  const rotation = face?.rotation || face?.angle || {};
  const yaw = Math.abs(Number(rotation.yaw ?? 0));
  const pitch = Math.abs(Number(rotation.pitch ?? 0));
  const roll = Math.abs(Number(rotation.roll ?? 0));
  const anglePenalty = clamp(1 - (yaw + pitch + roll) / 135);

  return clamp(
    detection * 0.35 +
    size * 0.30 +
    centered * 0.20 +
    anglePenalty * 0.15
  );
}

export function validEmbedding(value, dimensions = null) {
  if (!value || typeof value.length !== 'number' || value.length < 1 || value.length > 4096 ||
      (dimensions !== null && value.length !== dimensions)) return false;
  let scale = 0;
  for (const n of value) {
    if (typeof n !== 'number' || !Number.isFinite(n)) return false;
    scale = Math.max(scale,Math.abs(n));
  }
  return scale > 0;
}

export function cosineSimilarity(a, b) {
  if (!validEmbedding(a) || !validEmbedding(b,a.length)) return 0;
  const scaleA = Math.max(...Array.from(a,Math.abs));
  const scaleB = Math.max(...Array.from(b,Math.abs));
  let dot = 0, aa = 0, bb = 0;
  for (let i = 0; i < a.length; i += 1) {
    const av = a[i] / scaleA, bv = b[i] / scaleB;
    dot += av * bv; aa += av * av; bb += bv * bv;
  }
  return clamp(dot / (Math.sqrt(aa) * Math.sqrt(bb)), -1, 1);
}

export function robustProfileSimilarity(embedding, references, topK = 2) {
  const scores = (references || [])
    .filter(reference => validEmbedding(reference,embedding?.length))
    .map((reference) => cosineSimilarity(embedding, reference))
    .filter(Number.isFinite)
    .sort((a, b) => b - a);

  if (!scores.length) return 0;
  const count = Math.max(1, Math.min(topK, scores.length));
  return scores.slice(0, count).reduce((sum, score) => sum + score, 0) / count;
}

export function bestParticipantMatch(
  embedding,
  participants,
  threshold = MATCH_THRESHOLD,
  minMargin = 0.04,
  minSamples = 3
) {
  const candidates = [];
  if (!validEmbedding(embedding)) return {matched:false,participant:null,similarity:0,secondSimilarity:0,margin:0,ambiguous:false};

  for (const participant of participants || []) {
    if (participant.recognitionEnabled === false) continue;
    const references = (participant.embeddings || []).filter(v => validEmbedding(v,embedding.length));
    if (references.length < minSamples) continue;

    candidates.push({
      participant,
      similarity: robustProfileSimilarity(embedding, references)
    });
  }

  candidates.sort((a, b) => b.similarity - a.similarity);
  const best = candidates[0] || null;
  const second = candidates[1] || null;
  const margin = best ? best.similarity - (second?.similarity || 0) : 0;
  const matched = Boolean(best && best.similarity >= threshold && margin >= minMargin);

  return {
    matched,
    participant: matched ? best.participant : null,
    similarity: best?.similarity || 0,
    secondSimilarity: second?.similarity || 0,
    margin,
    ambiguous: Boolean(best && best.similarity >= threshold && margin < minMargin)
  };
}

export function createTrack(id, detection, now = 0) {
  return {
    id,
    box: detection.box,
    cx: detection.box.cx,
    cy: detection.box.cy,
    firstSeenAt: now,
    lastSeenAt: now,
    scanProgress: 0,
    quality: detection.quality || 0,
    samples: 0,
    embedding: detection.embedding || null,
    participantId: null,
    participantName: null,
    similarity: 0,
    status: 'detected',
    latestPhoto: null
  };
}

export function distanceBetweenTrackAndDetection(track, detection) {
  return Math.hypot(
    Number(track.cx) - Number(detection.box.cx),
    Number(track.cy) - Number(detection.box.cy)
  );
}

export function assignTracks(previousTracks, detections, now = 0, options = {}) {
  const maxDistance = options.maxDistance ?? MAX_TRACK_DISTANCE;
  const nextId = options.nextId || (() => 'T' + String(Math.floor(Math.random() * 9999)).padStart(4, '0'));

  // A spatial track is not identity evidence. Expired tracks cannot reclaim identity.
  const maxAge = options.maxAgeMs ?? 1500;
  const remainingTracks = [...(previousTracks || [])].filter(track =>
    Number.isFinite(track.lastSeenAt) && now >= track.lastSeenAt && now - track.lastSeenAt <= maxAge);
  const assignedTrackIds = new Set();
  const output = [];

  for (const detection of detections || []) {
    let bestTrack = null;
    let bestDistance = Infinity;

    for (const track of remainingTracks) {
      if (assignedTrackIds.has(track.id)) continue;
      const distance = distanceBetweenTrackAndDetection(track, detection);
      if (distance < bestDistance && distance <= maxDistance) {
        bestDistance = distance;
        bestTrack = track;
      }
    }

    if (!bestTrack) {
      bestTrack = createTrack(nextId(), detection, now);
    } else {
      bestTrack = {
        ...bestTrack,
        box: detection.box,
        cx: detection.box.cx,
        cy: detection.box.cy,
        quality: detection.quality || 0,
        embedding: detection.embedding || null,
        participantId:null,participantName:null,similarity:0,
        lastSeenAt: now
      };
    }

    assignedTrackIds.add(bestTrack.id);
    output.push(bestTrack);
  }

  return output;
}

export function advanceScan(track, options = {}) {
  const quality = clamp(track.quality || 0);
  const minQuality = options.minQuality ?? 0.50;
  const increment = options.increment ?? 18;
  const decay = options.decay ?? 5;

  const progress = quality >= minQuality
    ? Math.min(100, (track.scanProgress || 0) + increment * quality)
    : Math.max(0, (track.scanProgress || 0) - decay);

  let status = 'scanning';
  if (quality < minQuality) status = 'align-face';
  if (progress >= 100) status = 'ready';

  return {
    ...track,
    scanProgress: progress,
    samples: (track.samples || 0) + (quality >= minQuality ? 1 : 0),
    status
  };
}

export function participantRecord(input = {}) {
  const now = input.now || new Date().toISOString();
  return {
    id: String(input.id || cryptoRandomId()),
    name: String(input.name || '').trim(),
    nickname: String(input.nickname || '').trim(),
    notes: String(input.notes || '').trim(),
    primaryPhoto: input.primaryPhoto || null,
    latestPhoto: input.latestPhoto || null,
    embeddings: Array.isArray(input.embeddings) ? input.embeddings.map((v) => Array.from(v)) : [],
    recognitionEnabled: input.recognitionEnabled !== false,
    // Owner opt-in provenance survives the existing participant persistence
    // boundary. This is local-only metadata, never evidence of Cloud enrollment.
    visualEnrollment: input.visualEnrollment?.scope === 'owner-self'
      && typeof input.visualEnrollment.consentedAt === 'string'
      && !Number.isNaN(Date.parse(input.visualEnrollment.consentedAt))
      ? {scope:'owner-self',consentedAt:input.visualEnrollment.consentedAt,
          automatic:input.visualEnrollment.automatic===true,
          trackingEnabled:false,cloudSync:false,
          contactCreation:'requires_owner_approval'}
      : null,
    voiceEmbeddings: Array.isArray(input.voiceEmbeddings) ? input.voiceEmbeddings.map((v) => Array.from(v)) : [],
    voiceRecognitionEnabled: input.voiceRecognitionEnabled !== false,
    voiceProfileSamples: Array.isArray(input.voiceProfileSamples) ? input.voiceProfileSamples : [],
    voiceProfileReady: input.voiceProfileReady === true,
    voiceUpdatedAt: input.voiceUpdatedAt || null,
    createdAt: input.createdAt || now,
    updatedAt: now,
    lastSeenAt: input.lastSeenAt || null,
    gamesPlayed: Number(input.gamesPlayed || 0)
  };
}

export function cryptoRandomId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return 'p-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

