#!/usr/bin/env node
/**
 * Automated Email Status Reporter for Jobs Automation Pipeline
 * Responsive, mobile-first HTML email reporter sent via Resend API.
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
      naukri: { name: 'Naukri', applied: 0, skipped: 0, stopped: 0, reason: '' },
      linkedin: { name: 'LinkedIn', applied: 0, skipped: 0, stopped: 0, reason: '' },
      hirist: { name: 'Hirist', applied: 0, skipped: 0, stopped: 0, reason: '' },
      instahyre: { name: 'Instahyre', applied: 0, skipped: 0, stopped: 0, reason: '' },
      wellfound: { name: 'Wellfound', applied: 0, skipped: 0, stopped: 0, reason: '' },
      apna: { name: 'Apna', applied: 0, skipped: 0, stopped: 0, reason: '' },
      uplers: { name: 'Uplers', applied: 0, skipped: 0, stopped: 0, reason: '' },
      glassdoor: { name: 'Glassdoor', applied: 0, skipped: 0, stopped: 0, reason: '' },
      foundit: { name: 'Foundit', applied: 0, skipped: 0, stopped: 0, reason: '' },
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

  // Check log for common session/challenge notices in the current run cycle only
  if (fs.existsSync(LOG_PATH)) {
    const fullLog = fs.readFileSync(LOG_PATH, 'utf-8');
    const lastRunIdx = fullLog.lastIndexOf('STARTING SEQUENTIAL JOB APPLICATION PIPELINE');
    const logContent = lastRunIdx !== -1 ? fullLog.slice(lastRunIdx) : fullLog.slice(-50000);

    // Naukri alert
    if (stats.byPlatform.naukri.applied === 0) {
      if (logContent.includes('session expired / login prompt') || stats.byPlatform.naukri.reason.includes('login')) {
        stats.alerts.push({
          portal: 'Naukri',
          level: 'warning',
          message: 'Session expired. Run `python login_capture.py naukri` to refresh cookies.'
        });
      } else if (logContent.includes('access denied') || stats.byPlatform.naukri.reason.includes('access denied')) {
        stats.alerts.push({
          portal: 'Naukri',
          level: 'warning',
          message: 'WAF/Bot check challenge detected on search. Safely paused for this cycle.'
        });
      }
    }

    // Foundit alert
    if (stats.byPlatform.foundit.applied === 0 && logContent.includes('verification challenge detected on Foundit') && !stats.alerts.some(a => a.portal === 'Foundit')) {
      stats.alerts.push({
        portal: 'Foundit',
        level: 'info',
        message: 'Security challenge detected on search. Safely paused for this cycle.'
      });
    }

    // Glassdoor alert
    if (stats.byPlatform.glassdoor.applied === 0 && logContent.includes('Bot check detected on Glassdoor') && !stats.alerts.some(a => a.portal === 'Glassdoor')) {
      stats.alerts.push({
        portal: 'Glassdoor',
        level: 'info',
        message: 'Bot check detected on search. Safely paused for this cycle.'
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

// 4. Build 100% Mobile-Responsive HTML Email Template
function generateHtmlReport(stats, externalJobs) {
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
  const dateStr = now.toLocaleDateString('en-IN', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'Asia/Kolkata' });

  let alertsHtml = '';
  if (stats.alerts.length > 0) {
    alertsHtml = `
      <div style="margin: 18px 0; padding: 12px 16px; background-color: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 6px;">
        <div style="font-weight: 700; color: #b45309; font-size: 13px; margin-bottom: 6px;">Action & Security Notices</div>
        <ul style="margin: 0; padding-left: 18px; color: #92400e; font-size: 12px; line-height: 1.5;">
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
      ? '<span style="display:inline-block; background: #dcfce7; color: #15803d; padding: 2px 8px; border-radius: 10px; font-weight: 600; font-size: 10px;">Applied</span>'
      : (data.stopped > 0
        ? '<span style="display:inline-block; background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 10px; font-weight: 600; font-size: 10px;">Paused</span>'
        : '<span style="display:inline-block; background: #f1f5f9; color: #64748b; padding: 2px 8px; border-radius: 10px; font-size: 10px;">Active</span>');

    return `
      <tr style="border-bottom: 1px solid #f1f5f9;">
        <td style="padding: 10px 8px; font-weight: 600; color: #0f172a; font-size: 12px;">${p.name}</td>
        <td style="padding: 10px 8px; text-align: center;">${statusBadge}</td>
        <td style="padding: 10px 8px; text-align: center; font-weight: bold; color: ${data.applied > 0 ? '#15803d' : '#475569'}; font-size: 12px;">${data.applied}</td>
        <td style="padding: 10px 8px; text-align: center; color: #94a3b8; font-size: 12px;">${data.skipped}</td>
      </tr>
    `;
  }).join('');

  let extJobsHtml = '';
  if (externalJobs.length > 0) {
    extJobsHtml = `
      <div style="margin-top: 24px;">
        <h3 style="margin: 0 0 10px; color: #0f172a; font-size: 14px; font-weight: 600;">🎯 Discovered External ATS Jobs</h3>
        <table style="width: 100%; border-collapse: collapse;">
          ${externalJobs.map(j => `
            <tr style="border-bottom: 1px solid #f1f5f9;">
              <td style="padding: 8px 4px;">
                <div style="font-weight: 600; font-size: 12px; color: #2563eb;">
                  <a href="${j.url}" target="_blank" style="color: #2563eb; text-decoration: none;">${j.title}</a>
                </div>
                <div style="color: #64748b; font-size: 11px;">${j.company} • ${j.location}</div>
              </td>
              <td style="padding: 8px 4px; text-align: right; vertical-align: middle;">
                <a href="${j.url}" target="_blank" style="display: inline-block; background: #2563eb; color: #ffffff; padding: 4px 10px; border-radius: 4px; text-decoration: none; font-size: 11px; font-weight: 600; white-space: nowrap;">Apply &rarr;</a>
              </td>
            </tr>
          `).join('')}
        </table>
      </div>
    `;
  }

  return `
  <!DOCTYPE html>
  <html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>Jobs Automation Status Report</title>
    <style>
      /* Mobile Resets & Responsive Rules */
      body, table, td, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
      table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
      img { -ms-interpolation-mode: bicubic; }
      
      @media screen and (max-width: 600px) {
        .email-wrapper { width: 100% !important; padding: 6px !important; }
        .email-card { border-radius: 6px !important; border: none !important; }
        .header-box { padding: 18px 14px !important; }
        .header-title { font-size: 18px !important; }
        .content-box { padding: 14px 12px !important; }
        .stat-grid-col { display: block !important; width: 100% !important; max-width: 100% !important; margin-bottom: 8px !important; }
        .platform-table th, .platform-table td { padding: 8px 4px !important; font-size: 11px !important; }
      }
    </style>
  </head>
  <body style="margin: 0; padding: 12px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
    
    <div class="email-wrapper" style="max-width: 580px; margin: 0 auto;">
      <div class="email-card" style="background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.05);">
        
        <!-- Header Banner -->
        <div class="header-box" style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 22px 24px; color: #ffffff;">
          <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #94a3b8; font-weight: 600;">Autonomous Jobs Pipeline</div>
          <h1 class="header-title" style="margin: 4px 0 0; font-size: 20px; font-weight: 700; color: #ffffff;">Status & Applications Report</h1>
          <div style="margin-top: 4px; font-size: 12px; color: #cbd5e1;">Cycle completed at <strong>${timeStr} IST</strong> • ${dateStr}</div>
        </div>

        <div class="content-box" style="padding: 20px 24px;">
          
          <!-- Responsive Metric Cards Grid -->
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 16px;">
            <tr>
              <td class="stat-grid-col" style="width: 32%; vertical-align: top;">
                <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px 8px; text-align: center;">
                  <div style="font-size: 24px; font-weight: 700; color: #16a34a; line-height: 1.1;">${stats.appliedToday}</div>
                  <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: #15803d; margin-top: 3px;">Applied Today</div>
                </div>
              </td>
              <td style="width: 2%;"></td>
              <td class="stat-grid-col" style="width: 32%; vertical-align: top;">
                <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 8px; text-align: center;">
                  <div style="font-size: 24px; font-weight: 700; color: #475569; line-height: 1.1;">${stats.skippedToday}</div>
                  <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: #64748b; margin-top: 3px;">Deduplicated</div>
                </div>
              </td>
              <td style="width: 2%;"></td>
              <td class="stat-grid-col" style="width: 32%; vertical-align: top;">
                <div style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; padding: 12px 8px; text-align: center;">
                  <div style="font-size: 24px; font-weight: 700; color: #2563eb; line-height: 1.1;">${externalJobs.length}</div>
                  <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: #1d4ed8; margin-top: 3px;">External ATS</div>
                </div>
              </td>
            </tr>
          </table>

          ${alertsHtml}

          <!-- Platform Status Breakdown Table -->
          <h3 style="margin: 18px 0 8px; color: #0f172a; font-size: 14px; font-weight: 600;">📊 Platform Status Breakdown</h3>
          <div style="width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch;">
            <table class="platform-table" role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse: collapse; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden;">
              <thead>
                <tr style="background: #f8fafc; border-bottom: 2px solid #e2e8f0; color: #475569; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">
                  <th style="padding: 8px 10px; text-align: left;">Portal</th>
                  <th style="padding: 8px 10px; text-align: center;">Activity</th>
                  <th style="padding: 8px 10px; text-align: center;">Applied</th>
                  <th style="padding: 8px 10px; text-align: center;">Skipped</th>
                </tr>
              </thead>
              <tbody>
                ${platformRows}
              </tbody>
            </table>
          </div>

          ${extJobsHtml}

          <!-- Footer -->
          <div style="margin-top: 24px; padding-top: 14px; border-top: 1px solid #e2e8f0; text-align: center; color: #94a3b8; font-size: 11px; line-height: 1.4;">
            Autonomous Jobs Pipeline on macOS • Runs daily at <strong>8:30 AM</strong>, <strong>11:30 AM</strong>, <strong>2:30 PM</strong>.<br>
            All applied jobs are recorded permanently in <code>applications_log.csv</code>.
          </div>

        </div>
      </div>
    </div>
  </body>
  </html>
  `;
}

function generatePipelineStartedHtml(cycleName, nowStr) {
  return `
  <!DOCTYPE html>
  <html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pipeline Started: ${cycleName}</title>
    <style>
      body { margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; }
      .container { max-width: 600px; margin: 20px auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); }
      .header { background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); padding: 24px 20px; color: #ffffff; text-align: center; }
      .badge { display: inline-block; padding: 4px 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 9999px; background: rgba(255, 255, 255, 0.2); color: #ffffff; margin-bottom: 8px; }
      .title { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
      .subtitle { margin: 6px 0 0; font-size: 13px; color: #e0f2fe; }
      .body { padding: 24px 20px; color: #334155; }
      .card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px; margin-bottom: 16px; }
      .pill { display: inline-block; padding: 4px 8px; font-size: 11px; font-weight: 600; border-radius: 4px; background: #e0f2fe; color: #0369a1; margin: 3px 2px; }
      .footer { margin-top: 24px; padding-top: 14px; border-top: 1px solid #e2e8f0; text-align: center; color: #94a3b8; font-size: 11px; }
    </style>
  </head>
  <body>
    <div style="padding: 10px;">
      <div class="container">
        <div class="header">
          <div class="badge">🚀 Execution Active</div>
          <h1 class="title">Jobs Automation Pipeline Started</h1>
          <div class="subtitle">${cycleName} • ${nowStr}</div>
        </div>
        <div class="body">
          <div class="card" style="border-left: 4px solid #0284c7;">
            <div style="font-size: 13px; font-weight: 700; color: #0369a1; margin-bottom: 4px;">🎯 30-Minute Early Outreach Advantage</div>
            <div style="font-size: 13px; color: #475569; line-height: 1.5;">
              Running ahead of peak business hours (8:30 AM / 11:30 AM / 2:30 PM) so your tailored profile and resume land at the <strong>very top of recruiter inboxes</strong> before hiring teams open their laptops at 9:30–10:00 AM.
            </div>
          </div>

          <h3 style="margin: 18px 0 8px; color: #0f172a; font-size: 14px; font-weight: 600;">⚡ Active Pipeline Tasks in This Cycle</h3>
          <ul style="margin: 0; padding-left: 20px; font-size: 13px; color: #475569; line-height: 1.6;">
            <li><strong>Profile Activity Refresh:</strong> Minor headline updates on Naukri & LinkedIn to trigger platform activity algorithms and boost recruiter search appearances.</li>
            <li><strong>Dynamic ATS Resume Compilation:</strong> JD-tailored 1-page PDF resumes built on the fly for LinkedIn EasyApply postings.</li>
            <li><strong>Stealth Multi-Portal Applications:</strong> Running across 9 portals in headless mode with automated form screening and CTC handling.</li>
            <li><strong>Scam Protection:</strong> Filtering spam/reposting agencies like Jobgether/Jobtogether.</li>
          </ul>

          <h3 style="margin: 18px 0 8px; color: #0f172a; font-size: 14px; font-weight: 600;">🌐 Portals Being Processed</h3>
          <div style="margin-bottom: 16px;">
            <span class="pill">LinkedIn EasyApply</span>
            <span class="pill">LinkedIn Recruiter Outreach</span>
            <span class="pill">Naukri</span>
            <span class="pill">Foundit (Monster)</span>
            <span class="pill">Glassdoor (Indeed SmartApply)</span>
            <span class="pill">Instahyre</span>
            <span class="pill">Hirist</span>
            <span class="pill">Wellfound</span>
            <span class="pill">Uplers</span>
            <span class="pill">Apna</span>
          </div>

          <div class="card" style="background: #ecfdf5; border: 1px solid #a7f3d0; color: #065f46; font-size: 12px; line-height: 1.5;">
            <strong>📬 What happens next:</strong> As soon as this application sweep finishes, a detailed completion summary email with exact counts and job links will be delivered to you automatically.
          </div>

          <div class="footer">
            Autonomous Jobs Pipeline on macOS • Operating in 24/7 Server Mode<br>
            Cycles: <strong>8:30 AM</strong> • <strong>11:30 AM</strong> • <strong>2:30 PM</strong>
          </div>
        </div>
      </div>
    </div>
  </body>
  </html>
  `;
}

// 5. Send Email via Resend API
async function main() {
  const isStarted = process.argv.includes('--started');

  if (isStarted) {
    const now = new Date();
    const hour = now.getHours();
    let cycleName = 'Early Outreach Cycle';
    if (hour < 11) {
      cycleName = 'Morning Run (8:30 AM)';
    } else if (hour < 14) {
      cycleName = 'Mid-Day Run (11:30 AM)';
    } else {
      cycleName = 'Afternoon Run (2:30 PM)';
    }
    const nowStr = now.toLocaleDateString('en-IN', {
      timeZone: 'Asia/Kolkata',
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true
    });

    const htmlContent = generatePipelineStartedHtml(cycleName, nowStr);
    console.log(`[+] Sending pipeline start notification email to ${TO_EMAIL}...`);

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
          subject: `🚀 [Started] Jobs Automation Pipeline Active — ${cycleName}`,
          html: htmlContent
        })
      });

      const data = await res.json();
      if (res.ok) {
        console.log(`[+] SUCCESS: Start notification email sent to ${TO_EMAIL}! Message ID: ${data.id}`);
      } else {
        console.error(`[!] Failed to send start email via Resend:`, data);
      }
    } catch (err) {
      console.error(`[!] Network error sending start email:`, err.message);
    }
    return;
  }

  const stats = getTodayStats();
  const externalJobs = getExternalJobs();
  const htmlContent = generateHtmlReport(stats, externalJobs);

  console.log(`[+] Preparing responsive status update for: ${TO_EMAIL}...`);

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
      console.log(`[+] SUCCESS: Mobile-responsive status email sent to ${TO_EMAIL}! Message ID: ${data.id}`);
    } else {
      console.error(`[!] Failed to send email via Resend:`, data);
    }
  } catch (err) {
    console.error(`[!] Network error sending email:`, err.message);
  }
}

main();
