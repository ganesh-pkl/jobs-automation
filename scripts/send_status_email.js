#!/usr/bin/env node
/**
 * Automated Email Status Reporter for Jobs Automation Pipeline
 * Sends rich HTML email reports via Resend API to your personal email.
 */

const fs = require('fs');
const path = require('path');

const PROJECT_DIR = path.resolve(__dirname, '..');
const ENV_PATH = path.join(PROJECT_DIR, '.env');
const LOG_PATH = path.join(PROJECT_DIR, 'logs', 'scheduled_run.log');
const APP_LOG_PATH = path.join(PROJECT_DIR, 'applications_log.csv');
const EXT_JOBS_PATH = path.join(PROJECT_DIR, 'external_jobs.csv');

// 1. Load environment variables from .env
function loadEnv() {
  const env = {};
  if (fs.existsSync(ENV_PATH)) {
    const lines = fs.readFileSync(ENV_PATH, 'utf-8').split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const idx = trimmed.indexOf('=');
      if (idx !== -1) {
        const key = trimmed.slice(0, idx).trim();
        let val = trimmed.slice(idx + 1).trim();
        if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
          val = val.slice(1, -1);
        }
        env[key] = val;
      }
    }
  }
  return env;
}

const env = loadEnv();
const API_KEY = process.env.RESEND_API_KEY || env.RESEND_API_KEY;
const TO_EMAIL = process.env.NOTIFICATION_EMAIL || env.NOTIFICATION_EMAIL || 'ganeshpirikirala@gmail.com';

if (!API_KEY) {
  console.error('[!] RESEND_API_KEY not found in .env or environment. Skipping email.');
  process.exit(0);
}

// 2. Parse applications_log.csv for today's statistics
function getTodayStats() {
  const todayStr = new Date().toISOString().slice(0, 10);
  const stats = {
    appliedToday: 0,
    skippedToday: 0,
    stoppedToday: 0,
    byPlatform: {
      naukri: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      linkedin: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      hirist: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      uplers: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      instahyre: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      foundit: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      wellfound: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      glassdoor: { applied: 0, skipped: 0, stopped: 0, reason: '' },
      apna: { applied: 0, skipped: 0, stopped: 0, reason: '' },
    },
    alerts: []
  };

  if (fs.existsSync(APP_LOG_PATH)) {
    const content = fs.readFileSync(APP_LOG_PATH, 'utf-8');
    const lines = content.split('\n');
    for (const line of lines) {
      if (!line.trim() || line.startsWith('timestamp')) continue;
      const parts = line.split(',');
      if (parts.length >= 5) {
        const ts = parts[0].trim();
        const source = parts[1].trim().toLowerCase();
        const status = parts[4].trim().toLowerCase();
        const reason = parts.slice(5).join(',').trim();

        if (ts.startsWith(todayStr)) {
          if (status === 'applied') {
            stats.appliedToday++;
            if (stats.byPlatform[source]) stats.byPlatform[source].applied++;
          } else if (status === 'skipped') {
            stats.skippedToday++;
            if (stats.byPlatform[source]) stats.byPlatform[source].skipped++;
          } else if (status === 'stopped') {
            stats.stoppedToday++;
            if (stats.byPlatform[source]) {
              stats.byPlatform[source].stopped++;
              stats.byPlatform[source].reason = reason;
            }
          }
        }
      }
    }
  }

  // Check log for common session/challenge notices
  if (fs.existsSync(LOG_PATH)) {
    const logContent = fs.readFileSync(LOG_PATH, 'utf-8');
    if (logContent.includes('access denied') && !stats.alerts.some(a => a.portal === 'Naukri')) {
      stats.alerts.push({
        portal: 'Naukri',
        level: 'warning',
        message: 'Session expired (access denied). Run `python login_capture.py naukri` to refresh cookies.'
      });
    }
    if (logContent.includes('verification challenge detected on Foundit') && !stats.alerts.some(a => a.portal === 'Foundit')) {
      stats.alerts.push({
        portal: 'Foundit',
        level: 'info',
        message: 'Security challenge encountered. Safely paused for this cycle.'
      });
    }
    if (logContent.includes('Bot check detected on Glassdoor') && !stats.alerts.some(a => a.portal === 'Glassdoor')) {
      stats.alerts.push({
        portal: 'Glassdoor',
        level: 'info',
        message: 'Bot check detected on Glassdoor search. Paused safely.'
      });
    }
  }

  return stats;
}

// 3. Parse external_jobs.csv
function getExternalJobs() {
  const jobs = [];
  if (fs.existsSync(EXT_JOBS_PATH)) {
    const content = fs.readFileSync(EXT_JOBS_PATH, 'utf-8');
    const lines = content.split('\n');
    for (let i = 1; i < lines.length && jobs.length < 5; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      const cols = line.split(',');
      if (cols.length >= 8) {
        jobs.push({
          platform: cols[0],
          title: cols[1],
          company: cols[2],
          location: cols[3],
          url: cols[7] || cols[6]
        });
      }
    }
  }
  return jobs;
}

// 4. Build Responsive HTML Email Template
function generateHtmlReport(stats, externalJobs) {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
  const dateStr = now.toLocaleDateString('en-IN', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'Asia/Kolkata' });

  let alertsHtml = '';
  if (stats.alerts.length > 0) {
    alertsHtml = `
      <div style="margin: 20px 0; padding: 14px 18px; background-color: #fff8e6; border-left: 4px solid #f59e0b; border-radius: 6px;">
        <h3 style="margin: 0 0 8px; color: #b45309; font-size: 15px; font-weight: 600;">⚠️ Action & Security Notices</h3>
        <ul style="margin: 0; padding-left: 20px; color: #92400e; font-size: 13px; line-height: 1.5;">
          ${stats.alerts.map(a => `<li><strong>${a.portal}:</strong> ${a.message}</li>`).join('')}
        </ul>
      </div>
    `;
  }

  const platforms = [
    { name: 'Naukri', key: 'naukri' },
    { name: 'LinkedIn', key: 'linkedin' },
    { name: 'Hirist', key: 'hirist' },
    { name: 'Instahyre', key: 'instahyre' },
    { name: 'Wellfound', key: 'wellfound' },
    { name: 'Apna', key: 'apna' },
    { name: 'Uplers', key: 'uplers' },
    { name: 'Glassdoor', key: 'glassdoor' },
    { name: 'Foundit', key: 'foundit' },
  ];

  let platformRows = platforms.map(p => {
    const data = stats.byPlatform[p.key] || { applied: 0, skipped: 0, stopped: 0, reason: '' };
    const statusBadge = data.applied > 0
      ? '<span style="background: #e6f4ea; color: #137333; padding: 3px 8px; border-radius: 12px; font-weight: 600; font-size: 11px;">Active Apply</span>'
      : (data.stopped > 0
        ? '<span style="background: #fce8e6; color: #c5221f; padding: 3px 8px; border-radius: 12px; font-weight: 600; font-size: 11px;">Paused / Check</span>'
        : '<span style="background: #f1f3f4; color: #5f6368; padding: 3px 8px; border-radius: 12px; font-size: 11px;">Explored</span>');

    return `
      <tr style="border-bottom: 1px solid #f1f3f4;">
        <td style="padding: 10px 12px; font-weight: 600; color: #202124;">${p.name}</td>
        <td style="padding: 10px 12px; text-align: center;">${statusBadge}</td>
        <td style="padding: 10px 12px; text-align: center; font-weight: bold; color: ${data.applied > 0 ? '#137333' : '#5f6368'};">${data.applied}</td>
        <td style="padding: 10px 12px; text-align: center; color: #70757a;">${data.skipped}</td>
      </tr>
    `;
  }).join('');

  let extJobsHtml = '';
  if (externalJobs.length > 0) {
    extJobsHtml = `
      <div style="margin-top: 24px;">
        <h3 style="margin: 0 0 12px; color: #202124; font-size: 15px; font-weight: 600;">🎯 Fresh External ATS Opportunities (Actionable Queue)</h3>
        <table style="width: 100%; border-collapse: collapse; font-size: 12px;">
          ${externalJobs.map(j => `
            <tr style="border-bottom: 1px solid #f1f3f4;">
              <td style="padding: 8px 10px;">
                <div style="font-weight: 600; color: #1a73e8;">
                  <a href="${j.url}" target="_blank" style="color: #1a73e8; text-decoration: none;">${j.title}</a>
                </div>
                <div style="color: #5f6368; font-size: 11px;">${j.company} • ${j.location} (${j.platform})</div>
              </td>
              <td style="padding: 8px 10px; text-align: right;">
                <a href="${j.url}" target="_blank" style="display: inline-block; background: #1a73e8; color: #ffffff; padding: 4px 10px; border-radius: 4px; text-decoration: none; font-size: 11px; font-weight: 600;">Apply Now &rarr;</a>
              </td>
            </tr>
          `).join('')}
        </table>
      </div>
    `;
  }

  return `
  <!DOCTYPE html>
  <html>
  <head>
    <meta charset="utf-8">
    <title>Jobs Automation Status</title>
  </head>
  <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
    <div style="max-width: 620px; margin: 0 auto; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.04);">
      
      <!-- Header Banner -->
      <div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); padding: 24px 28px; color: #ffffff;">
        <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 1.2px; color: #94a3b8; font-weight: 600;">Autonomous Pipeline Report</div>
        <h1 style="margin: 6px 0 0; font-size: 22px; font-weight: 700; color: #ffffff;">Multi-Portal Jobs Automation</h1>
        <div style="margin-top: 6px; font-size: 13px; color: #cbd5e1;">Run completed at <strong>${timeStr} IST</strong> on ${dateStr}</div>
      </div>

      <div style="padding: 24px 28px;">
        
        <!-- Summary Cards Grid -->
        <table style="width: 100%; border-collapse: separate; border-spacing: 12px 0; margin-bottom: 20px;">
          <tr>
            <td style="width: 33.3%; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 14px; text-align: center;">
              <div style="font-size: 26px; font-weight: 700; color: #16a34a;">${stats.appliedToday}</div>
              <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; color: #15803d; margin-top: 4px;">Applied Today</div>
            </td>
            <td style="width: 33.3%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; text-align: center;">
              <div style="font-size: 26px; font-weight: 700; color: #475569;">${stats.skippedToday}</div>
              <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; color: #64748b; margin-top: 4px;">Deduplicated</div>
            </td>
            <td style="width: 33.3%; background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; padding: 14px; text-align: center;">
              <div style="font-size: 26px; font-weight: 700; color: #2563eb;">${externalJobs.length}</div>
              <div style="font-size: 11px; font-weight: 600; text-transform: uppercase; color: #1d4ed8; margin-top: 4px;">External ATS</div>
            </td>
          </tr>
        </table>

        ${alertsHtml}

        <!-- Platforms Breakdown Table -->
        <h3 style="margin: 20px 0 10px; color: #0f172a; font-size: 15px; font-weight: 600;">📊 Platform Status Breakdown</h3>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; border-radius: 6px; overflow: hidden; border: 1px solid #e2e8f0;">
          <thead>
            <tr style="background: #f8fafc; border-bottom: 2px solid #e2e8f0; color: #475569; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">
              <th style="padding: 10px 12px; text-align: left;">Portal</th>
              <th style="padding: 10px 12px; text-align: center;">Activity</th>
              <th style="padding: 10px 12px; text-align: center;">Applied</th>
              <th style="padding: 10px 12px; text-align: center;">Skipped</th>
            </tr>
          </thead>
          <tbody>
            ${platformRows}
          </tbody>
        </table>

        ${extJobsHtml}

        <!-- Footer -->
        <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; text-align: center; color: #94a3b8; font-size: 11px;">
          Generated automatically by your local Jobs Automation runner on macOS.<br>
          Next scheduled cycles: <strong>8:30 AM</strong> • <strong>1:30 PM</strong> • <strong>4:30 PM</strong> daily.
        </div>

      </div>
    </div>
  </body>
  </html>
  `;
}

// 5. Send Email via Resend API
async function main() {
  const stats = getTodayStats();
  const externalJobs = getExternalJobs();
  const htmlContent = generateHtmlReport(stats, externalJobs);

  console.log(`[+] Preparing email status update for: ${TO_EMAIL}...`);
  console.log(`    Applied Today: ${stats.appliedToday} | Skipped: ${stats.skippedToday} | External ATS: ${externalJobs.length}`);

  try {
    const res = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        from: 'Jobs Automation <onboarding@resend.dev>',
        to: [TO_EMAIL],
        subject: `[Status] Jobs Automation Run Summary — ${stats.appliedToday} Applied Today`,
        html: htmlContent
      })
    });

    const data = await res.json();
    if (res.ok) {
      console.log(`[+] SUCCESS: Status email sent to ${TO_EMAIL}! Message ID: ${data.id}`);
    } else {
      console.error(`[!] Failed to send email via Resend:`, data);
    }
  } catch (err) {
    console.error(`[!] Network error sending email:`, err.message);
  }
}

main();
