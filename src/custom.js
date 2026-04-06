import { PlaywrightCrawler, sleep } from 'crawlee';

/**
 * Scrapes job listings from any custom URL provided by the user.
 * Uses smart extraction — tries common job board patterns first,
 * then falls back to full-page text analysis.
 *
 * @param {object} options
 * @param {string} options.url
 * @param {string} options.keyword
 * @param {string} options.location
 * @param {number} options.maxJobs
 * @param {object} options.proxyConfiguration
 * @returns {Promise<Array>} Array of job objects
 */
export async function scrapeCustomUrl({ url, keyword, location, maxJobs, proxyConfiguration }) {
  const jobs = [];
  const hostname = new URL(url).hostname;

  const crawler = new PlaywrightCrawler({
    proxyConfiguration,
    headless: true,
    navigationTimeoutSecs: 60,
    requestHandlerTimeoutSecs: 120,
    maxRequestsPerCrawl: maxJobs + 10,

    launchContext: {
      launchOptions: {
        args: ['--no-sandbox', '--disable-setuid-sandbox'],
      },
    },

    async requestHandler({ page, request }) {
      if (request.label === 'LIST') {
        await sleep(2000);

        console.log(`   Analyzing page structure of: ${hostname}`);

        // ── Try to detect job cards on the page ──────────────────────────
        const jobLinks = await detectJobLinks(page, keyword);

        if (jobLinks.length > 0) {
          console.log(`   Detected ${jobLinks.length} job links on ${hostname}`);
          const linksToVisit = jobLinks.slice(0, maxJobs);
          for (const link of linksToVisit) {
            await crawler.addRequests([{ url: link, label: 'DETAIL', userData: { source: hostname } }]);
          }
        } else {
          // No individual links found — try to extract jobs directly from this page
          console.log(`   No job links detected — extracting directly from page...`);
          const pageJobs = await extractJobsFromPage(page, hostname, keyword, location);
          jobs.push(...pageJobs.slice(0, maxJobs));
        }
      }

      if (request.label === 'DETAIL') {
        await sleep(randomDelay(1000, 2500));
        const source = request.userData?.source || hostname;
        const job = await extractSingleJob(page, source);
        if (job) {
          // Filter by keyword/location if possible
          const titleMatch = job.title.toLowerCase().includes(keyword.toLowerCase()) ||
                             job.description.toLowerCase().includes(keyword.toLowerCase());
          const locationMatch = !location ||
                                job.location.toLowerCase().includes(location.toLowerCase()) ||
                                job.location === 'N/A';

          if (titleMatch || locationMatch) {
            jobs.push(job);
            console.log(`   ✔  ${job.title} @ ${job.company}`);
          }
        }
      }
    },

    failedRequestHandler({ request, error }) {
      console.error(`   ✘  Failed: ${request.url} — ${error.message}`);
    },
  });

  await crawler.run([{ url, label: 'LIST' }]);
  return jobs;
}

// ── Smart Job Link Detector ───────────────────────────────────────────────────

async function detectJobLinks(page, keyword) {
  return await page.evaluate((kw) => {
    const links = new Set();

    // Common patterns for job listing links
    const jobPatterns = [
      /\/job[s]?\//i,
      /\/career[s]?\//i,
      /\/position[s]?\//i,
      /\/opening[s]?\//i,
      /\/vacancy/i,
      /\/role[s]?\//i,
      /job[-_]?id/i,
      /listing/i,
    ];

    const allAnchors = Array.from(document.querySelectorAll('a[href]'));

    for (const anchor of allAnchors) {
      const href = anchor.href;
      const text = anchor.innerText?.toLowerCase() || '';

      // Skip non-http links
      if (!href.startsWith('http')) continue;

      // Match URL pattern OR link text contains keyword
      const urlMatchesJob = jobPatterns.some((p) => p.test(href));
      const textMatchesKeyword = kw && text.includes(kw.toLowerCase());

      if (urlMatchesJob || textMatchesKeyword) {
        links.add(href);
      }
    }

    return Array.from(links);
  }, keyword);
}

// ── Single Job Page Extractor ─────────────────────────────────────────────────

async function extractSingleJob(page, source) {
  try {
    return await page.evaluate((src) => {
      const getText = (selectors) => {
        for (const sel of selectors) {
          const el = document.querySelector(sel);
          if (el && el.innerText?.trim()) return el.innerText.trim();
        }
        return 'N/A';
      };

      const title = getText([
        'h1[class*="title"]',
        'h1[class*="job"]',
        'h1[class*="position"]',
        '.job-title',
        '.position-title',
        'h1',
      ]);

      const company = getText([
        '[class*="company"]',
        '[class*="employer"]',
        '[class*="org"]',
        '[itemprop="hiringOrganization"]',
        '.company-name',
      ]);

      const location = getText([
        '[class*="location"]',
        '[class*="city"]',
        '[itemprop="jobLocation"]',
        '.job-location',
      ]);

      const salary = getText([
        '[class*="salary"]',
        '[class*="compensation"]',
        '[class*="pay"]',
        '[class*="wage"]',
      ]);

      const jobType = getText([
        '[class*="job-type"]',
        '[class*="employment-type"]',
        '[class*="contract"]',
      ]);

      const description = getText([
        '[class*="description"]',
        '[class*="details"]',
        '[class*="content"]',
        '.job-description',
        'article',
        'main',
      ]);

      // Try to get bullet-point requirements
      const reqContainers = document.querySelectorAll(
        '[class*="requirement"], [class*="qualification"], [class*="skills"]'
      );
      let requirements = [];
      for (const container of reqContainers) {
        const items = container.querySelectorAll('li');
        if (items.length > 0) {
          requirements = Array.from(items).map((li) => li.innerText.trim());
          break;
        }
      }

      // Fallback: grab all list items near description
      if (requirements.length === 0) {
        const allLists = document.querySelectorAll('ul li');
        requirements = Array.from(allLists)
          .map((li) => li.innerText.trim())
          .filter((t) => t.length > 10 && t.length < 300)
          .slice(0, 10);
      }

      if (!title || title === 'N/A') return null;

      return {
        source: src,
        title,
        company,
        location,
        salary,
        jobType,
        description: description.substring(0, 1500),
        requirements,
        url: window.location.href,
        posted: 'N/A',
      };
    }, source);
  } catch {
    return null;
  }
}

// ── Full-Page Job Extractor (fallback) ────────────────────────────────────────

async function extractJobsFromPage(page, source, keyword, location) {
  try {
    const jobs = await page.evaluate((src, kw, loc) => {
      // Look for repeated job card structures
      const cardSelectors = [
        '[class*="job-card"]',
        '[class*="job-listing"]',
        '[class*="job-result"]',
        '[class*="job-item"]',
        '[class*="career-item"]',
        'article',
        '[data-job-id]',
        '[data-listing-id]',
      ];

      let cards = [];
      for (const sel of cardSelectors) {
        const found = document.querySelectorAll(sel);
        if (found.length > 0) {
          cards = Array.from(found);
          break;
        }
      }

      return cards.slice(0, 30).map((card) => {
        const text   = card.innerText || '';
        const lines  = text.split('\n').map((l) => l.trim()).filter(Boolean);
        const title  = lines[0] || 'Unknown Title';
        const company = lines[1] || 'N/A';
        const loc    = lines.find((l) => l.includes(',') || l.toLowerCase().includes('remote')) || loc || 'N/A';

        return {
          source: src,
          title,
          company,
          location: loc,
          salary: 'Not listed',
          jobType: 'N/A',
          description: text.substring(0, 800),
          requirements: [],
          url: window.location.href,
          posted: 'N/A',
        };
      });
    }, source, keyword, location);

    return jobs.filter(
      (j) =>
        j.title.toLowerCase().includes(keyword.toLowerCase()) ||
        j.description.toLowerCase().includes(keyword.toLowerCase())
    );
  } catch {
    return [];
  }
}

function randomDelay(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}
