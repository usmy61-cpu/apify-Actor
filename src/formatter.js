/**
 * Formats an array of job objects into human-readable plain text blocks.
 * Each job is separated by a dashed divider.
 */
export function formatJobsAsText(jobs, { keyword, location }) {
  const divider = '-'.repeat(60);
  const header  = buildHeader(jobs.length, keyword, location);
  const blocks  = jobs.map((job, index) => formatSingleJob(job, index + 1));

  return [header, ...blocks].join('\n');
}

// ── Header ────────────────────────────────────────────────────────────────────

function buildHeader(total, keyword, location) {
  const now = new Date().toUTCString();
  return [
    '============================================================',
    '           JOB SCRAPER RESULTS',
    '============================================================',
    `  Keyword   : ${keyword}`,
    `  Location  : ${location}`,
    `  Total Jobs: ${total}`,
    `  Generated : ${now}`,
    '============================================================',
    '',
  ].join('\n');
}

// ── Single Job Block ──────────────────────────────────────────────────────────

function formatSingleJob(job, index) {
  const divider = '-'.repeat(60);

  const lines = [
    divider,
    `  JOB #${index}`,
    divider,
    `  Job Title   : ${clean(job.title)}`,
    `  Company     : ${clean(job.company)}`,
    `  Location    : ${clean(job.location)}`,
    `  Job Type    : ${clean(job.jobType)}`,
    `  Salary      : ${clean(job.salary)}`,
    `  Posted      : ${clean(job.posted)}`,
    `  Source      : ${clean(job.source)}`,
    `  URL         : ${clean(job.url)}`,
    '',
  ];

  // Description
  if (job.description && job.description !== 'N/A') {
    lines.push('  DESCRIPTION :');
    const wrapped = wordWrap(clean(job.description), 56);
    for (const line of wrapped) {
      lines.push(`    ${line}`);
    }
    lines.push('');
  }

  // Requirements
  if (job.requirements && job.requirements.length > 0) {
    lines.push('  REQUIREMENTS:');
    for (const req of job.requirements) {
      if (req && req.trim()) {
        lines.push(`    • ${clean(req)}`);
      }
    }
    lines.push('');
  }

  return lines.join('\n');
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function clean(text) {
  if (!text || text === 'N/A' || text === 'undefined') return 'Not listed';
  return String(text)
    .replace(/\s+/g, ' ')     // collapse whitespace
    .replace(/\n+/g, ' ')     // remove newlines
    .trim();
}

function wordWrap(text, maxWidth) {
  const words  = text.split(' ');
  const lines  = [];
  let current  = '';

  for (const word of words) {
    if ((current + ' ' + word).trim().length <= maxWidth) {
      current = (current + ' ' + word).trim();
    } else {
      if (current) lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  return lines;
}
