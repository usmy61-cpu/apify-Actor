import { Actor } from 'apify';
import { scrapeLinkedIn } from './linkedin.js';
import { scrapeCustomUrl } from './custom.js';
import { formatJobsAsText } from './formatter.js';

await Actor.init();

// ── Load Input ────────────────────────────────────────────────────────────────
const input = await Actor.getInput();

const {
  keyword        = '',
  location       = '',
  scrapeLinkedIn: doLinkedIn = true,
  customUrls     = [],
  maxJobsPerSource = 20,
  proxyConfig    = { useApifyProxy: true, apifyProxyGroups: ['RESIDENTIAL'] },
} = input || {};

if (!keyword || !location) {
  throw new Error('❌  Please provide both "keyword" and "location" in the input.');
}

console.log(`\n🔍  Searching for: "${keyword}" in "${location}"`);
console.log(`📦  Max jobs per source: ${maxJobsPerSource}\n`);

// ── Create Proxy ──────────────────────────────────────────────────────────────
const proxyConfiguration = await Actor.createProxyConfiguration(proxyConfig);

// ── Collect All Jobs ──────────────────────────────────────────────────────────
let allJobs = [];

// 1. LinkedIn
if (doLinkedIn) {
  console.log('🔗  Starting LinkedIn scraper...');
  try {
    const linkedinJobs = await scrapeLinkedIn({
      keyword,
      location,
      maxJobs: maxJobsPerSource,
      proxyConfiguration,
    });
    console.log(`   ✅  LinkedIn: found ${linkedinJobs.length} jobs`);
    allJobs = allJobs.concat(linkedinJobs);
  } catch (err) {
    console.error(`   ⚠️  LinkedIn scraper failed: ${err.message}`);
  }
}

// 2. Custom URLs
for (const url of customUrls) {
  console.log(`🌐  Scraping custom URL: ${url}`);
  try {
    const customJobs = await scrapeCustomUrl({
      url,
      keyword,
      location,
      maxJobs: maxJobsPerSource,
      proxyConfiguration,
    });
    console.log(`   ✅  Custom site: found ${customJobs.length} jobs`);
    allJobs = allJobs.concat(customJobs);
  } catch (err) {
    console.error(`   ⚠️  Custom URL scraper failed for ${url}: ${err.message}`);
  }
}

console.log(`\n📋  Total jobs collected: ${allJobs.length}`);

// ── Format & Save Output ──────────────────────────────────────────────────────
if (allJobs.length === 0) {
  console.log('⚠️  No jobs found. Try different keyword, location, or check your proxy settings.');
} else {
  const plainText = formatJobsAsText(allJobs, { keyword, location });

  // Save as plain text file in Key-Value Store
  await Actor.setValue('OUTPUT', plainText, { contentType: 'text/plain; charset=utf-8' });

  // Also push each job to the dataset (useful for viewing in Apify Console)
  for (const job of allJobs) {
    await Actor.pushData(job);
  }

  console.log('\n✅  Done! Download your results:');
  console.log('   → Key-Value Store → OUTPUT  (plain text file)');
  console.log('   → Dataset  (structured records)\n');
}

await Actor.exit();
