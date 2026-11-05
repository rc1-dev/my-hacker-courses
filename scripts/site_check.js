/* Headless check of the docs/ site (Playwright, pre-installed in cloud sessions).
 * Usage: python3 -m http.server -d docs 8765 &  node scripts/site_check.js [screenshot dir]
 * Exercises filters, search, likes, For You, shortcuts, course/dead/category/timeline/
 * library/table pages and phone width; fails on console errors (YouTube and
 * archive.org hosts ignored, sandboxes often block them). Course ids are from the
 * current data: update them if the candidates change. */
const { chromium } = require('playwright');
const BASE = 'http://localhost:8765/';
const SHOT = process.argv[2];
const IGNORE = /ytimg\.com|youtube|google|archive\.org/;
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !IGNORE.test(m.text() + (m.location().url || ''))) errors.push(m.text() + ' @ ' + m.location().url); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('requestfailed', r => { if (!IGNORE.test(r.url())) errors.push('failed: ' + r.url()); });
  const ok = (cond, msg) => { console.log((cond ? 'PASS ' : 'FAIL ') + msg); if (!cond) process.exitCode = 1; };

  await page.goto(BASE); await page.waitForSelector('.grid .card');
  ok(await page.locator('.grid .card').count() === 60, 'home: first page of 60 cards');
  ok(await page.locator('.card img').count() === 0, 'home: cards are text only (no images)');
  ok(await page.locator('.for-you .card').count() === 0, 'home: no For You before any likes');
  const total = await page.locator('.count').textContent();
  await page.getByLabel('Free only').check(); await page.waitForTimeout(100);
  const free = await page.locator('.count').textContent();
  ok(free !== total, `filter free only: ${total} -> ${free}`);
  await page.getByLabel('Video lectures').check(); await page.waitForTimeout(100);
  ok(/video=1/.test(page.url()), 'filters kept in URL: ' + page.url().replace(BASE, ''));
  const vids = await page.locator('.grid .card').count();
  const allVideo = await page.locator('.grid .card').evaluateAll(cs => cs.every(c => /Video lectures/.test(c.textContent)));
  ok(vids > 0 && allVideo, `video filter: ${vids} cards, all tagged`);
  await page.getByLabel('Video lectures').uncheck(); await page.getByLabel('Free only').uncheck();
  await page.getByLabel('Has assignments').check(); await page.waitForTimeout(100);
  ok(+(await page.locator('.count').textContent()).split(' ')[0] > 0, 'assignments filter: ' + await page.locator('.count').textContent());
  await page.getByLabel('Has assignments').uncheck();
  await page.getByLabel('Provider').selectOption('Coursera'); await page.waitForTimeout(100);
  ok((await page.locator('.grid .card .kicker').allTextContents()).every(t => t.startsWith('Coursera')), 'provider filter: Coursera only');
  await page.getByLabel('Provider').selectOption('');
  const catOpt = await page.locator('select[aria-label=Category] option').nth(1).getAttribute('value');
  await page.getByLabel('Category').selectOption(catOpt); await page.waitForTimeout(100);
  ok(/cat=/.test(page.url()), 'category filter: ' + catOpt + ' -> ' + await page.locator('.count').textContent());
  await page.getByLabel('Category').selectOption('');
  await page.locator('.chips .chip').nth(1).click(); await page.waitForTimeout(100);
  ok(/area=/.test(page.url()), 'area chip: ' + await page.locator('.count').textContent());
  await page.locator('.chips .chip').first().click();
  // keyboard: / search
  await page.keyboard.press('/'); await page.keyboard.type('compilers'); await page.waitForTimeout(250);
  const n = await page.locator('.grid .card').count();
  ok(n > 0 && n < 30, `search "compilers": ${n} cards`);
  await page.fill('#q', ''); await page.waitForTimeout(250); await page.keyboard.press('Escape');
  // j/k + l
  await page.keyboard.press('j'); await page.keyboard.press('j');
  const selId = await page.locator('.card.kb').getAttribute('data-id');
  ok(selId && (await page.locator('.card.kb').count()) === 1, 'j selects a card: ' + selId);
  await page.keyboard.press('l');
  ok(await page.locator(`.card[data-id="${selId}"] .like`).getAttribute('aria-pressed') === 'true', 'l likes the selected card');
  await page.keyboard.press('k');
  await page.keyboard.press('?');
  ok(await page.locator('dialog.help').evaluate(d => d.open), '? opens the shortcut help');
  await page.keyboard.press('Escape');
  // like by clicking a heart
  await page.locator('.grid .card .like').nth(5).click();
  await page.reload(); await page.waitForSelector('.grid .card');
  const fy = await page.locator('.for-you .card').count();
  ok(fy > 0, `For You after 2 likes: ${fy} cards`);
  if (SHOT) await page.screenshot({ path: SHOT + '/home.png' });
  // g l -> library
  await page.keyboard.press('g'); await page.keyboard.press('l'); await page.waitForURL(/library/);
  await page.waitForSelector('.lib-head');
  ok((await page.locator('.lib-head h2').allTextContents()).join(',') === 'For you,Liked,History', 'library sections');
  ok(await page.locator('main .grid').nth(1).locator('.card').count() === 2, 'library: 2 liked');

  // course page with lectures
  await page.goto(BASE + 'course.html?c=mit-edu-6-001'); await page.waitForSelector('.comment');
  ok(await page.locator('.lecture').count() >= 1, 'course: Lectures on HackerTube: ' + await page.locator('.lecture h3').allTextContents());
  ok(await page.locator('.comment').count() >= 10, 'course: HN comments ' + await page.locator('.comment').count());
  ok(await page.locator('.side .card').count() >= 5, 'course: similar courses ' + await page.locator('.side .card').count());
  ok(await page.locator('.comment a[href*="news.ycombinator.com/item"]').count() >= 10, 'course: comments link to HN');
  await page.keyboard.press('l');
  ok(await page.locator('.actions [data-like]').getAttribute('aria-pressed') === 'true', 'course: l likes this course');
  if (SHOT) await page.screenshot({ path: SHOT + '/course.png', fullPage: false });
  // dead course
  await page.goto(BASE + 'course.html?c=cs50-harvard-edu-x'); await page.waitForSelector('.comment');
  const href = await page.locator('.actions .btn.primary').getAttribute('href');
  ok(/web\.archive\.org/.test(href) && await page.locator('.note').count() === 1, 'dead course: Wayback link + note');
  if (SHOT) await page.screenshot({ path: SHOT + '/dead.png' });
  await page.goto(BASE + 'course.html?c=nope'); await page.waitForSelector('.empty');
  ok(/not found/.test(await page.locator('.empty').textContent()), 'unknown course id handled');

  await page.goto(BASE + 'category.html'); await page.waitForSelector('.area');
  await page.locator('.area li a').first().click(); await page.waitForSelector('.grid .card');
  ok(await page.locator('.grid .card').count() > 0, 'category page: ' + page.url().replace(BASE, ''));
  await page.goto(BASE + 'timeline.html'); await page.waitForSelector('.year');
  ok(await page.locator('.year').count() > 10, 'timeline years ' + await page.locator('.year').count());
  await page.goto(BASE + 'library.html'); await page.waitForSelector('.lib-head');
  ok(await page.locator('main .grid').nth(2).locator('.card').count() === 2, 'history recorded (2 course pages)');
  await page.goto(BASE + 'table.html'); await page.waitForSelector('#rows tr td a');
  ok(await page.locator('#rows tr').count() > 100, 'table page still works');
  // phone width: no horizontal scroll
  const m = await browser.newPage({ viewport: { width: 390, height: 800 } });
  for (const p of ['', 'course.html?c=mit-edu-6-001', 'timeline.html', 'library.html', 'category.html']) {
    await m.goto(BASE + p); await m.waitForTimeout(700);
    const w = await m.evaluate(() => document.documentElement.scrollWidth);
    ok(w <= 390, `phone ${p || 'home'}: scrollWidth ${w}`);
  }
  if (SHOT) { await m.goto(BASE + 'course.html?c=mit-edu-6-001'); await m.waitForTimeout(800); await m.screenshot({ path: SHOT + '/phone.png' }); }
  ok(errors.length === 0, 'no console errors' + (errors.length ? ':\n  ' + errors.join('\n  ') : ''));
  await browser.close();
})();
