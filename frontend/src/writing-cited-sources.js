// Read the rendered citation registry, never the selected material pool or
// unused evidence records. Numbers belong to the whole manuscript.
export function writingCitedSources(preview, sectionId) {
  const sources = new Map();
  for (const section of preview?.sections || []) {
    if (sectionId !== undefined && section.id !== sectionId) continue;
    for (const paragraph of section.paragraphs || []) {
      for (const citation of paragraph.citations || []) {
        const evidence = citation.evidence;
        if (citation.status !== 'registered' || !Number.isInteger(citation.number) || citation.number < 1
            || !evidence?.sourceId || !evidence.revisionId) continue;
        const key = JSON.stringify([evidence.sourceId, evidence.revisionId]);
        if (!sources.has(key)) sources.set(key, {
          key, number: citation.number, sourceId: evidence.sourceId, revisionId: evidence.revisionId,
          title: evidence.title, evidence, sourceStatus: citation.sourceStatus, citations: [],
        });
        const source = sources.get(key);
        if (!source.citations.some(item => item.sectionId === section.id && item.citation.id === citation.id)) {
          source.citations.push({ sectionId: section.id, paragraphId: paragraph.id, citation });
        }
      }
    }
  }
  return [...sources.values()].sort((first, second) => first.number - second.number);
}
