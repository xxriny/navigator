export const artifactMarker = (kind, id) => `[[navigator-share:${kind}:${id}]]`;

export function parseArtifactMarker(content) {
  const match = /^\[\[navigator-share:(components|apis):([A-Za-z0-9-]{1,64})\]\]$/.exec(content || "");
  return match ? { kind: match[1], id: match[2] } : null;
}
