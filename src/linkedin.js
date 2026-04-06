import { PlaywrightCrawler, sleep } from 'crawlee';

/**
 * Scrapes LinkedIn Jobs search results page.
 * @param {object} options
 * @param {string} options.keyword
 * @param {string} options.location
 * @param {number} options.maxJobs
 * @param {object} options.proxyConfiguration
 * @returns {Promise<Array>} Array of job objects
 */
export async function scrapeLinkedIn({ keyword, location, maxJobs, proxyConfiguration }) {
  const jobs = [];

  // Build LinkedIn search URL
  const searchUrl = buildLinkedInUrl(keyword, location);
  console.log(`   LinkedIn URL: ${searchUrl}`);

  const crawler = new PlaywrightCrawler({
    proxyConfiguration,
    headless: true,
    navigationTimeoutSecs: 60,
    requestHandlerTimeoutSecs: 120,

    // Use a real browser fingerprint to avoid detection
    launchContext: {
      launchOptions: {
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-blink-features=AutomationControlled',
        ],
      },
    },

    async requestHandler({ page, request }) {
      // ── Handle LinkedIn Job List Page ────────────────────────────────────
      if (request.label === 'LIST') {
        console.log('   Loaded LinkedIn search results page...');

        // Wait for job cards to appear
        await page.waitForSelector('.jobs-search__results-list', { timeout: 30000 }).catch(() => {
          console.log('   ⚠️  Could not find jobs list container — page may be blocked or changed.');
        });

        // Scroll down to load more jobs
        await autoScroll(page);
        await sleep(2000);

        // Collect all job card links
        const jobLinks = await page.$$eval(
          'ul.jobs-search__results-list > li a.base-card__full-link',
          (anchors) => anchors.map((a) => a.href.split('?')[0]).filter(Boolean)
        );

        console.log(`   Found ${jobLinks.length} job links on LinkedIn`);

        const linksToVisit = jobLinks.slice(0, maxJobs);

        // Add each job detail page to the queue
        for (const link of linksToVisit) {
          await crawler.addRequests([{ url: link, label: 'DETAIL' }]);
        }
      }

      // ── Handle LinkedIn Job Detail Page ──────────────────────────────────
      if (request.label === 'DETAIL') {
        await sleep(randomDelay(1000, 3000));

        const job = await page.evaluate(() => {
          const getText = (selector) =>
            document.querySelector(selector)?.innerText?.trim() || 'N/A';

          const title       = getText('h1.top-card-layout__title');
          const company     = getText('a.topcard__org-name-link') || getText('.topcard__org-name');
          const location    = getText('.topcard__flavor--bullet');
          const jobType     = getText('.description__job-criteria-text--criteria');
          const description = getText('.show-more-less-html__markup');

          // Try to extract salary
          const salaryEl = document.querySelector('[class*="salary"], [class*="compensation"]');
          const salary = salaryEl ? salaryEl.innerText.trim() : 'Not listed';

          // Posted date
          const posted = getText('time') || getText('.posted-time-ago__text');

          // Extract requirements from description bullet points
          const bullets = Array.from(
            document.querySelectorAll('.show-more-less-html__markup ul li')
          ).map((el) => el.innerText.trim());

          return {
            source: 'LinkedIn',
            title,
            company,
            location,
            jobType,
            salary,
            posted,
            description,
            requirements: bullets,
            url: window.location.href,
          };
        });

        if (job.title && job.title !== 'N/A') {
          jobs.push(job);
          console.log(`   ✔  ${job.title} @ ${job.company}`);
        }
      }
    },

    failedRequestHandler({ request, error }) {
      console.error(`   ✘  Failed: ${request.url} — ${error.message}`);
    },
  });

  await crawler.run([{ url: searchUrl, label: 'LIST' }]);

  return jobs;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildLinkedInUrl(keyword, location) {
  const base = 'https://www.linkedin.com/jobs/search/';
  const params = new URLSearchParams({
    keywords: keyword,
    location: location,
    trk: 'public_jobs_jobs-search-bar_search-submit',
    position: 1,
    pageNum: 0,
  });
  return `${base}?${params.toString()}`;
}

async function autoScroll(page) {
  await page.evaluate(async () => {
    await new Promise((resolve) => {
      let totalHeight = 0;
      const distance  = 500;
      const timer     = setInterval(() => {
        window.scrollBy(0, distance);
        totalHeight += distance;
        if (totalHeight >= 5000) {
          clearInterval(timer);
          resolve();
        }
      }, 300);
    });
  });
}

function randomDelay(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}
