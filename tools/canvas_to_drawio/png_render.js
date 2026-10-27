// usage: node png_render.js jobs.json out.json
// jobs: [{key, svg, w, h, scale}] -> out: {key: base64 PNG}, each SVG rendered on a transparent background.
const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const pngByKey = {};
  for (const job of jobs) {
    await page.setViewportSize({ width: Math.ceil(job.w * job.scale) + 2, height: Math.ceil(job.h * job.scale) + 2 });
    await page.setContent(`<html><body style="margin:0;background:transparent">` +
      job.svg.replace('<svg ', `<svg style="display:block;width:${job.w * job.scale}px;height:${job.h * job.scale}px" `) + `</body></html>`);
    const element = await page.$('svg');
    pngByKey[job.key] = (await element.screenshot({ omitBackground: true })).toString('base64');
  }
  await browser.close();
  fs.writeFileSync(process.argv[3], JSON.stringify(pngByKey));
})();
