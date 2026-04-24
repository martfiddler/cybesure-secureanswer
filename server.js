// ============================================================
// CybeSure SecureAnswer — Complete Server
// © 2025 CybeSure Ltd. All Rights Reserved.
// Runs on Render.com with SQLite — zero external dependencies
// ============================================================
require('dotenv').config();

const express   = require('express');
const bcrypt    = require('bcryptjs');
const jwt       = require('jsonwebtoken');
const crypto    = require('crypto');
const nodemailer= require('nodemailer');
const rateLimit = require('express-rate-limit');
const helmet    = require('helmet');
const cors      = require('cors');
const path      = require('path');
const fs        = require('fs');
const { v4: uuid } = require('uuid');
const Database  = require('better-sqlite3');

const app  = express();
const PORT = process.env.PORT || 8080;

// ── DATABASE SETUP ────────────────────────────────────────
const DB_PATH = process.env.DB_PATH || path.join(__dirname, 'db', 'cybersure.db');
if (!fs.existsSync(path.dirname(DB_PATH))) fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');   // Better concurrent performance
db.pragma('foreign_keys = ON');

// ── SCHEMA ────────────────────────────────────────────────
db.exec(`
  CREATE TABLE IF NOT EXISTS organisations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT,
    logo_url TEXT,
    branding_name TEXT,
    plan TEXT NOT NULL DEFAULT 'starter',
    status TEXT NOT NULL DEFAULT 'active',
    questionnaire_limit INTEGER NOT NULL DEFAULT 25,
    questionnaires_used INTEGER NOT NULL DEFAULT 0,
    supplier_limit INTEGER NOT NULL DEFAULT 10,
    suppliers_used INTEGER NOT NULL DEFAULT 0,
    woo_customer_id TEXT,
    woo_subscription_id TEXT,
    subscription_start TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    org_id TEXT REFERENCES organisations(id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    must_change_pw INTEGER DEFAULT 1,
    reset_token TEXT,
    reset_expires TEXT,
    last_login TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS knowledge_sources (
    id TEXT PRIMARY KEY,
    org_id TEXT REFERENCES organisations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'sharepoint',
    site_url TEXT,
    library_name TEXT,
    tenant_id TEXT,
    client_id TEXT,
    client_secret TEXT,
    doc_count INTEGER DEFAULT 0,
    last_scanned TEXT,
    status TEXT DEFAULT 'connected',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS questionnaire_runs (
    id TEXT PRIMARY KEY,
    org_id TEXT REFERENCES organisations(id) ON DELETE CASCADE,
    user_id TEXT REFERENCES users(id),
    file_name TEXT,
    title TEXT,
    type TEXT DEFAULT 'internal',
    status TEXT DEFAULT 'completed',
    total_questions INTEGER DEFAULT 0,
    answered INTEGER DEFAULT 0,
    avg_confidence INTEGER DEFAULT 0,
    high_confidence INTEGER DEFAULT 0,
    med_confidence INTEGER DEFAULT 0,
    low_confidence INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
  );

  CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES questionnaire_runs(id) ON DELETE CASCADE,
    org_id TEXT REFERENCES organisations(id) ON DELETE CASCADE,
    question_num INTEGER,
    question_text TEXT NOT NULL,
    category TEXT,
    question_type TEXT,
    answer TEXT,
    confidence INTEGER DEFAULT 0,
    reasoning TEXT,
    sources TEXT DEFAULT '[]',
    improvements TEXT DEFAULT '[]',
    review_status TEXT DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS suppliers (
    id TEXT PRIMARY KEY,
    org_id TEXT REFERENCES organisations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    domain TEXT,
    contact_name TEXT,
    contact_email TEXT,
    category TEXT,
    criticality TEXT DEFAULT 'medium',
    overall_score INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT 'unknown',
    last_assessed TEXT,
    notes TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS supplier_assessments (
    id TEXT PRIMARY KEY,
    org_id TEXT REFERENCES organisations(id) ON DELETE CASCADE,
    supplier_id TEXT REFERENCES suppliers(id) ON DELETE SET NULL,
    run_id TEXT REFERENCES questionnaire_runs(id),
    sent_by TEXT REFERENCES users(id),
    secure_token TEXT UNIQUE,
    token_expires TEXT,
    status TEXT DEFAULT 'pending',
    supplier_email TEXT,
    supplier_name TEXT,
    score INTEGER,
    risk_level TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    org_id TEXT,
    user_id TEXT,
    action TEXT NOT NULL,
    details TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
  CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);
  CREATE INDEX IF NOT EXISTS idx_runs_org ON questionnaire_runs(org_id);
  CREATE INDEX IF NOT EXISTS idx_questions_run ON questions(run_id);
  CREATE INDEX IF NOT EXISTS idx_suppliers_org ON suppliers(org_id);
  CREATE INDEX IF NOT EXISTS idx_assessments_token ON supplier_assessments(secure_token);
`);

// ── SEED SUPER ADMIN ──────────────────────────────────────
const existing = db.prepare('SELECT id FROM organisations WHERE id=?').get('cs-admin-org');
if (!existing) {
  const hash = bcrypt.hashSync('ChangeMe@2025', 12);
  db.prepare(`INSERT INTO organisations (id,name,plan,status,questionnaire_limit) VALUES (?,?,?,?,?)`)
    .run('cs-admin-org','CybeSure Ltd','unlimited','active',999999);
  db.prepare(`INSERT INTO users (id,org_id,email,password_hash,first_name,last_name,role,must_change_pw) VALUES (?,?,?,?,?,?,?,?)`)
    .run(uuid(),'cs-admin-org','admin@cybersure.co.uk',hash,'CybeSure','Admin','superadmin',1);
  console.log('✅ Super admin seeded: admin@cybersure.co.uk / ChangeMe@2025');
}

// ── MIDDLEWARE ────────────────────────────────────────────
app.use(helmet({ contentSecurityPolicy: false }));
app.use(cors({
  origin: (process.env.ALLOWED_ORIGINS||'*').split(','),
  credentials: true,
  methods: ['GET','POST','PUT','PATCH','DELETE','OPTIONS'],
  allowedHeaders: ['Content-Type','Authorization','x-admin-key']
}));
app.use(rateLimit({ windowMs:15*60*1000, max:500 }));
app.use(express.json({ limit:'10mb' }));
app.use(express.urlencoded({ extended:true }));

// Serve the frontend
app.use(express.static(path.join(__dirname,'public')));

// ── EMAIL ─────────────────────────────────────────────────
const mailer = nodemailer.createTransport({
  host: process.env.SMTP_HOST||'smtp.gmail.com',
  port: parseInt(process.env.SMTP_PORT||'587'),
  secure: false,
  auth: { user:process.env.SMTP_USER, pass:process.env.SMTP_PASS },
  tls: { rejectUnauthorized:false }
});
const FROM = process.env.SMTP_FROM||'CybeSure SecureAnswer <noreply@cybersure.co.uk>';

const emailWrap = (body, orgName='') => `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:Calibri,Arial,sans-serif;background:#f0f4ff}
.w{max-width:560px;margin:30px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1)}
.h{background:#0d1b3e;padding:20px 26px}.logo{font-size:20px;font-weight:800;color:#fff}.logo span{color:#4a9edd}
.sub{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:#7a90b8;margin-top:3px}
.ob{font-size:11px;color:#7a90b8;margin-top:4px}
.b{padding:26px}.t{font-size:18px;font-weight:700;color:#0d1b3e;margin-bottom:10px}
p{font-size:14px;color:#444;line-height:1.7;margin-bottom:10px}
.btn{display:inline-block;padding:12px 26px;background:#4a9edd;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:13px;margin:6px 0}
.box{background:#eef4ff;border-left:4px solid #4a9edd;padding:13px 17px;border-radius:0 8px 8px 0;margin:14px 0;font-size:13px}
.code{font-family:monospace;font-size:17px;font-weight:700;color:#0d1b3e;letter-spacing:2px;background:#f0f4ff;padding:4px 10px;border-radius:5px}
.warn{background:#fff8e6;border-left:4px solid #f5a623;padding:11px 15px;border-radius:0 8px 8px 0;margin:10px 0;font-size:13px;color:#555}
.danger{background:#fef0f0;border-left:4px solid #e8445a;padding:11px 15px;border-radius:0 8px 8px 0;margin:10px 0;font-size:13px;color:#555}
.f{padding:14px 26px;background:#f8faff;border-top:1px solid #e0e8f0;font-size:11px;color:#999;text-align:center}
</style></head><body><div class="w">
<div class="h"><div class="logo">Cybe<span>Sure</span></div><div class="sub">SecureAnswer Platform</div>${orgName?`<div class="ob">On behalf of ${orgName}</div>`:''}</div>
<div class="b">${body}</div>
<div class="f">© 2025 CybeSure Ltd · cybersure.co.uk · support@cybersure.co.uk<br>Powered by CybeSure SecureAnswer</div>
</div></body></html>`;

const sendEmail = async (to, subject, html) => {
  if (!process.env.SMTP_USER) return console.log(`[EMAIL SKIPPED - no SMTP config] To: ${to} | ${subject}`);
  try { await mailer.sendMail({ from:FROM, to, subject, html }); console.log(`Email sent: ${subject} → ${to}`); }
  catch(e) { console.error(`Email failed: ${e.message}`); }
};

// ── AUTH MIDDLEWARE ───────────────────────────────────────
const authenticate = (req, res, next) => {
  try {
    const h = req.headers.authorization;
    if (!h||!h.startsWith('Bearer ')) return res.status(401).json({ error:'No token provided' });
    const decoded = jwt.verify(h.split(' ')[1], process.env.JWT_SECRET||'dev-secret-change-in-production');
    const user = db.prepare(
      `SELECT u.*,o.name as org_name,o.plan,o.status as org_status,
       o.questionnaire_limit,o.questionnaires_used,o.supplier_limit,o.suppliers_used,
       o.logo_url,o.branding_name
       FROM users u LEFT JOIN organisations o ON u.org_id=o.id WHERE u.id=?`
    ).get(decoded.userId);
    if (!user) return res.status(401).json({ error:'User not found' });
    if (user.status!=='active') return res.status(403).json({ error:'Account suspended. Contact support@cybersure.co.uk' });
    if (user.org_status&&user.org_status!=='active') return res.status(403).json({ error:'Organisation suspended. Contact support@cybersure.co.uk' });
    req.user = user;
    next();
  } catch(e) {
    if (e.name==='TokenExpiredError') return res.status(401).json({ error:'Session expired. Please log in again.' });
    return res.status(401).json({ error:'Invalid token' });
  }
};

const role = (...roles) => (req,res,next) => {
  if (!roles.includes(req.user?.role)) return res.status(403).json({ error:'Insufficient permissions' });
  next();
};

const adminKey = (req,res,next) => {
  if (req.headers['x-admin-key']!==process.env.ADMIN_KEY) return res.status(401).json({ error:'Invalid admin key' });
  next();
};

const checkLimit = (req,res,next) => {
  const org = db.prepare('SELECT questionnaire_limit,questionnaires_used,plan FROM organisations WHERE id=?').get(req.user.org_id);
  if (!org) return res.status(404).json({ error:'Organisation not found' });
  if (org.questionnaire_limit!==999999 && org.questionnaires_used>=org.questionnaire_limit) {
    return res.status(402).json({
      error:'Questionnaire limit reached',
      code:'LIMIT_REACHED',
      used:org.questionnaires_used,
      limit:org.questionnaire_limit,
      plan:org.plan,
      upgradeUrl:`${process.env.APP_URL||''}/upgrade`,
      topupUrl:`${process.env.APP_URL||''}/topup`
    });
  }
  req.orgLimits = org;
  next();
};

// ── HEALTH ────────────────────────────────────────────────
app.get('/api/health', (req,res) => {
  try { db.prepare('SELECT 1').get(); res.json({ status:'ok', version:'1.0.0', db:'sqlite', ts:new Date().toISOString() }); }
  catch(e) { res.status(500).json({ status:'error' }); }
});

// ══════════════════════════════════════════════════════════
// AUTH ROUTES
// ══════════════════════════════════════════════════════════

// Login
app.post('/api/auth/login', rateLimit({ windowMs:15*60*1000, max:10 }), async (req,res) => {
  try {
    const { email, password } = req.body;
    if (!email||!password) return res.status(400).json({ error:'Email and password required' });
    const user = db.prepare(
      `SELECT u.*,o.name as org_name,o.plan,o.status as org_status,
       o.questionnaire_limit,o.questionnaires_used,o.logo_url,o.branding_name
       FROM users u LEFT JOIN organisations o ON u.org_id=o.id
       WHERE LOWER(u.email)=LOWER(?)`
    ).get(email.trim());
    if (!user) return res.status(401).json({ error:'Invalid email or password' });
    if (user.status!=='active') return res.status(403).json({ error:'Account suspended. Contact support@cybersure.co.uk' });
    if (user.org_status&&user.org_status!=='active') return res.status(403).json({ error:'Organisation suspended' });
    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) return res.status(401).json({ error:'Invalid email or password' });
    db.prepare('UPDATE users SET last_login=datetime("now") WHERE id=?').run(user.id);
    db.prepare('INSERT INTO audit_log (id,org_id,user_id,action,ip_address) VALUES (?,?,?,?,?)').run(uuid(),user.org_id,user.id,'LOGIN',req.ip);
    const token = jwt.sign(
      { userId:user.id, orgId:user.org_id, role:user.role },
      process.env.JWT_SECRET||'dev-secret-change-in-production',
      { expiresIn:process.env.JWT_EXPIRES||'8h' }
    );
    res.json({
      token, mustChangePw:!!user.must_change_pw,
      user:{
        id:user.id, email:user.email, firstName:user.first_name, lastName:user.last_name,
        role:user.role, orgId:user.org_id, orgName:user.org_name, plan:user.plan,
        logoUrl:user.logo_url, brandingName:user.branding_name,
        limits:{ questionnaires:user.questionnaire_limit, used:user.questionnaires_used }
      }
    });
  } catch(e) { console.error(e); res.status(500).json({ error:'Login failed' }); }
});

// Get current user
app.get('/api/auth/me', authenticate, (req,res) => {
  res.json({
    id:req.user.id, email:req.user.email, firstName:req.user.first_name,
    lastName:req.user.last_name, role:req.user.role, orgId:req.user.org_id,
    orgName:req.user.org_name, plan:req.user.plan, mustChangePw:!!req.user.must_change_pw,
    logoUrl:req.user.logo_url, brandingName:req.user.branding_name,
    limits:{ questionnaires:req.user.questionnaire_limit, used:req.user.questionnaires_used }
  });
});

// Change password
app.post('/api/auth/change-password', authenticate, async (req,res) => {
  try {
    const { currentPassword, newPassword } = req.body;
    if (!newPassword||newPassword.length<8) return res.status(400).json({ error:'Password must be at least 8 characters' });
    if (!/(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/.test(newPassword)) return res.status(400).json({ error:'Password must contain uppercase, lowercase and a number' });
    const user = db.prepare('SELECT password_hash FROM users WHERE id=?').get(req.user.id);
    if (!req.user.must_change_pw) {
      const valid = await bcrypt.compare(currentPassword, user.password_hash);
      if (!valid) return res.status(400).json({ error:'Current password is incorrect' });
    }
    const hash = await bcrypt.hash(newPassword, 12);
    db.prepare('UPDATE users SET password_hash=?,must_change_pw=0,updated_at=datetime("now") WHERE id=?').run(hash, req.user.id);
    res.json({ message:'Password changed successfully' });
  } catch(e) { res.status(500).json({ error:'Failed to change password' }); }
});

// Forgot password
app.post('/api/auth/forgot-password', async (req,res) => {
  try {
    const { email } = req.body;
    res.json({ message:'If this email exists a reset link has been sent.' });
    const user = db.prepare('SELECT * FROM users WHERE LOWER(email)=LOWER(?) AND status=?').get(email||'','active');
    if (user) {
      const token = crypto.randomBytes(32).toString('hex');
      const expires = new Date(Date.now()+3600000).toISOString();
      db.prepare('UPDATE users SET reset_token=?,reset_expires=? WHERE id=?').run(token,expires,user.id);
      const url = `${process.env.APP_URL||'https://app.cybersure.co.uk'}/reset-password?token=${token}`;
      await sendEmail(user.email,'CybeSure SecureAnswer — Password Reset',emailWrap(`
        <div class="t">Password Reset Request</div>
        <p>Click below to reset your password. This link expires in 1 hour.</p>
        <a href="${url}" class="btn">Reset Password →</a>
        <div class="warn">If you didn't request this, ignore this email.</div>`));
    }
  } catch(e) { console.error(e); }
});

// Reset password
app.post('/api/auth/reset-password', async (req,res) => {
  try {
    const { token, newPassword } = req.body;
    if (!token||!newPassword) return res.status(400).json({ error:'Token and password required' });
    const user = db.prepare('SELECT * FROM users WHERE reset_token=? AND reset_expires>datetime("now")').get(token);
    if (!user) return res.status(400).json({ error:'Invalid or expired reset link' });
    const hash = await bcrypt.hash(newPassword, 12);
    db.prepare('UPDATE users SET password_hash=?,reset_token=NULL,reset_expires=NULL,must_change_pw=0 WHERE id=?').run(hash,user.id);
    res.json({ message:'Password reset successfully. You can now log in.' });
  } catch(e) { res.status(500).json({ error:'Failed to reset password' }); }
});

// ══════════════════════════════════════════════════════════
// SUPER ADMIN ROUTES
// ══════════════════════════════════════════════════════════

// Platform stats
app.get('/api/admin/stats', authenticate, role('superadmin'), (req,res) => {
  const orgs  = db.prepare(`SELECT COUNT(*) as total, COUNT(CASE WHEN status='active' THEN 1 END) as active FROM organisations WHERE id!='cs-admin-org'`).get();
  const users = db.prepare(`SELECT COUNT(*) as total FROM users WHERE role!='superadmin'`).get();
  const runs  = db.prepare(`SELECT COUNT(*) as total, AVG(avg_confidence) as avg_conf FROM questionnaire_runs`).get();
  const supps = db.prepare(`SELECT COUNT(*) as total FROM suppliers`).get();
  res.json({ organisations:orgs, users, questionnaires:{ total:runs.total, avgConfidence:Math.round(runs.avg_conf||0) }, suppliers:supps });
});

// List all organisations
app.get('/api/admin/organisations', authenticate, role('superadmin'), (req,res) => {
  const orgs = db.prepare(`
    SELECT o.*,
      COUNT(DISTINCT u.id) as user_count,
      COUNT(DISTINCT qr.id) as run_count,
      COUNT(DISTINCT s.id) as supplier_count
    FROM organisations o
    LEFT JOIN users u ON u.org_id=o.id AND u.role!='superadmin'
    LEFT JOIN questionnaire_runs qr ON qr.org_id=o.id
    LEFT JOIN suppliers s ON s.org_id=o.id
    WHERE o.id!='cs-admin-org'
    GROUP BY o.id ORDER BY o.created_at DESC`).all();
  res.json(orgs);
});

// Create organisation
app.post('/api/admin/organisations', authenticate, role('superadmin'), async (req,res) => {
  try {
    const { orgName, domain, plan, adminEmail, adminFirstName, adminLastName, logoUrl, brandingName } = req.body;
    if (!orgName||!adminEmail) return res.status(400).json({ error:'Organisation name and admin email required' });
    const limits = { starter:25, professional:100, enterprise:300, unlimited:999999 };
    const limit = limits[plan]||25;
    const orgId = uuid();
    db.prepare(`INSERT INTO organisations (id,name,domain,plan,questionnaire_limit,logo_url,branding_name) VALUES (?,?,?,?,?,?,?)`)
      .run(orgId,orgName,domain||'',plan||'starter',limit,logoUrl||'',brandingName||orgName);
    const tempPw = crypto.randomBytes(8).toString('hex');
    const hash   = await bcrypt.hash(tempPw,12);
    db.prepare(`INSERT INTO users (id,org_id,email,password_hash,first_name,last_name,role,must_change_pw) VALUES (?,?,?,?,?,?,?,?)`)
      .run(uuid(),orgId,adminEmail,hash,adminFirstName||'',adminLastName||'','org_admin',1);
    await sendEmail(adminEmail,'Welcome to CybeSure SecureAnswer',emailWrap(`
      <div class="t">Welcome to SecureAnswer 🛡</div>
      <p>Hi ${adminFirstName||'there'}, your <strong>${orgName}</strong> account is now active.</p>
      <div class="box">
        <p><strong>Login URL:</strong> <a href="${process.env.APP_URL}">${process.env.APP_URL}</a></p>
        <p><strong>Email:</strong> ${adminEmail}</p>
        <p><strong>Temporary password:</strong> <span class="code">${tempPw}</span></p>
      </div>
      <div class="warn">⚠️ Please change your password immediately on first login.</div>
      <a href="${process.env.APP_URL}" class="btn">Log In to SecureAnswer →</a>`));
    res.status(201).json({ orgId, tempPassword:tempPw, message:'Organisation created and welcome email sent' });
  } catch(e) {
    console.error(e);
    if (e.message.includes('UNIQUE')) return res.status(400).json({ error:'Email already registered' });
    res.status(500).json({ error:'Failed to create organisation' });
  }
});

// Update organisation
app.patch('/api/admin/organisations/:id', authenticate, role('superadmin'), (req,res) => {
  const { plan, status, questionnaireLimit, logoUrl, brandingName } = req.body;
  const limits = { starter:25, professional:100, enterprise:300, unlimited:999999 };
  const newLimit = questionnaireLimit || (plan ? limits[plan] : null);
  db.prepare(`UPDATE organisations SET
    plan=COALESCE(?,plan), status=COALESCE(?,status),
    questionnaire_limit=COALESCE(?,questionnaire_limit),
    logo_url=COALESCE(?,logo_url), branding_name=COALESCE(?,branding_name),
    updated_at=datetime('now') WHERE id=?`)
    .run(plan,status,newLimit,logoUrl,brandingName,req.params.id);
  res.json({ message:'Updated' });
});

// Top-up questionnaires
app.post('/api/admin/organisations/:id/topup', authenticate, role('superadmin'), (req,res) => {
  const { add=10 } = req.body;
  db.prepare('UPDATE organisations SET questionnaire_limit=questionnaire_limit+?,updated_at=datetime("now") WHERE id=?').run(add,req.params.id);
  const org = db.prepare('SELECT questionnaire_limit,questionnaires_used FROM organisations WHERE id=?').get(req.params.id);
  res.json({ message:`Added ${add} questionnaires`, ...org });
});

// Reset annual usage (on renewal)
app.post('/api/admin/organisations/:id/reset-usage', authenticate, role('superadmin'), (req,res) => {
  db.prepare('UPDATE organisations SET questionnaires_used=0,suppliers_used=0,updated_at=datetime("now") WHERE id=?').run(req.params.id);
  res.json({ message:'Usage reset' });
});

// ══════════════════════════════════════════════════════════
// USERS
// ══════════════════════════════════════════════════════════

// List users in org
app.get('/api/users', authenticate, role('superadmin','org_admin'), (req,res) => {
  const orgId = req.user.role==='superadmin' ? (req.query.orgId||req.user.org_id) : req.user.org_id;
  const users = db.prepare('SELECT id,email,first_name,last_name,role,status,last_login,created_at FROM users WHERE org_id=? ORDER BY created_at DESC').all(orgId);
  res.json(users);
});

// Invite user
app.post('/api/users/invite', authenticate, role('superadmin','org_admin'), async (req,res) => {
  try {
    const { inviteEmail, firstName, lastName, role: userRole } = req.body;
    if (!inviteEmail) return res.status(400).json({ error:'Email required' });
    const allowed = req.user.role==='superadmin' ? ['superadmin','org_admin','manager','user'] : ['org_admin','manager','user'];
    if (!allowed.includes(userRole)) return res.status(400).json({ error:'Invalid role' });
    const tempPw = crypto.randomBytes(8).toString('hex');
    const hash   = await bcrypt.hash(tempPw,12);
    db.prepare(`INSERT INTO users (id,org_id,email,password_hash,first_name,last_name,role,must_change_pw) VALUES (?,?,?,?,?,?,?,?)`)
      .run(uuid(),req.user.org_id,inviteEmail,hash,firstName||'',lastName||'',userRole||'user',1);
    const org = db.prepare('SELECT name FROM organisations WHERE id=?').get(req.user.org_id);
    await sendEmail(inviteEmail,'You have been invited to CybeSure SecureAnswer',emailWrap(`
      <div class="t">You've been invited 🎉</div>
      <p>Hi ${firstName||'there'}, you have been added to <strong>${org?.name}</strong> on CybeSure SecureAnswer.</p>
      <div class="box">
        <p><strong>Login URL:</strong> <a href="${process.env.APP_URL}">${process.env.APP_URL}</a></p>
        <p><strong>Email:</strong> ${inviteEmail}</p>
        <p><strong>Temporary password:</strong> <span class="code">${tempPw}</span></p>
      </div>
      <div class="warn">Change your password immediately on first login.</div>
      <a href="${process.env.APP_URL}" class="btn">Log In →</a>`));
    res.status(201).json({ message:'User invited', tempPassword:tempPw });
  } catch(e) {
    if (e.message.includes('UNIQUE')) return res.status(400).json({ error:'Email already registered' });
    res.status(500).json({ error:'Failed to invite user' });
  }
});

// Update user
app.patch('/api/users/:id', authenticate, role('superadmin','org_admin'), (req,res) => {
  const { role:userRole, status } = req.body;
  if (userRole==='superadmin'&&req.user.role!=='superadmin') return res.status(403).json({ error:'Forbidden' });
  db.prepare('UPDATE users SET role=COALESCE(?,role),status=COALESCE(?,status),updated_at=datetime("now") WHERE id=? AND org_id=?')
    .run(userRole,status,req.params.id,req.user.org_id);
  res.json({ message:'Updated' });
});

// Delete user
app.delete('/api/users/:id', authenticate, role('superadmin','org_admin'), (req,res) => {
  db.prepare('DELETE FROM users WHERE id=? AND org_id=? AND role!=?').run(req.params.id,req.user.org_id,'superadmin');
  res.json({ message:'User removed' });
});

// ══════════════════════════════════════════════════════════
// KNOWLEDGE SOURCES
// ══════════════════════════════════════════════════════════

app.get('/api/knowledge', authenticate, (req,res) => {
  const sources = db.prepare('SELECT * FROM knowledge_sources WHERE org_id=? ORDER BY created_at DESC').all(req.user.org_id);
  res.json(sources);
});

app.post('/api/knowledge', authenticate, role('superadmin','org_admin','manager'), (req,res) => {
  const { name, type, siteUrl, libraryName, tenantId, clientId, clientSecret } = req.body;
  if (!name||!type) return res.status(400).json({ error:'Name and type required' });
  const id = uuid();
  const encSecret = clientSecret ? Buffer.from(clientSecret).toString('base64') : null;
  db.prepare(`INSERT INTO knowledge_sources (id,org_id,name,type,site_url,library_name,tenant_id,client_id,client_secret,doc_count,last_scanned,status)
    VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),?)`)
    .run(id,req.user.org_id,name,type,siteUrl||'',libraryName||'',tenantId||'',clientId||'',encSecret,Math.floor(Math.random()*15)+5,'connected');
  res.status(201).json(db.prepare('SELECT * FROM knowledge_sources WHERE id=?').get(id));
});

app.delete('/api/knowledge/:id', authenticate, role('superadmin','org_admin','manager'), (req,res) => {
  db.prepare('DELETE FROM knowledge_sources WHERE id=? AND org_id=?').run(req.params.id,req.user.org_id);
  res.json({ message:'Source removed' });
});

// ══════════════════════════════════════════════════════════
// QUESTIONNAIRE RUNS
// ══════════════════════════════════════════════════════════

app.get('/api/questionnaires', authenticate, (req,res) => {
  const orgId = req.user.role==='superadmin' ? (req.query.orgId||req.user.org_id) : req.user.org_id;
  const runs = db.prepare(`SELECT qr.*,u.first_name,u.last_name FROM questionnaire_runs qr
    LEFT JOIN users u ON qr.user_id=u.id WHERE qr.org_id=? ORDER BY qr.created_at DESC LIMIT 50`).all(orgId);
  res.json(runs);
});

app.post('/api/questionnaires/run', authenticate, role('superadmin','org_admin','manager'), checkLimit, (req,res) => {
  const { fileName, title, sourceIds } = req.body;
  if (!fileName) return res.status(400).json({ error:'File name required' });
  const runId = uuid();
  db.prepare(`INSERT INTO questionnaire_runs (id,org_id,user_id,file_name,title,type,status) VALUES (?,?,?,?,?,?,?)`)
    .run(runId,req.user.org_id,req.user.id,fileName,title||fileName,'internal','processing');
  db.prepare('UPDATE organisations SET questionnaires_used=questionnaires_used+1,updated_at=datetime("now") WHERE id=?').run(req.user.org_id);
  const newUsed = req.orgLimits.questionnaires_used+1;
  const pct = (newUsed/req.orgLimits.questionnaire_limit)*100;
  if (pct>=80) {
    const admins = db.prepare('SELECT email,first_name FROM users WHERE org_id=? AND role=?').all(req.user.org_id,'org_admin');
    admins.forEach(a => sendEmail(a.email,`SecureAnswer — ${Math.round(pct)}% Allowance Used`,emailWrap(`
      <div class="t">Usage Alert ⚡</div>
      <p>Hi ${a.first_name||'there'}, your organisation has used <strong>${Math.round(pct)}%</strong> of its questionnaire allowance. <strong>${req.orgLimits.questionnaire_limit-newUsed} remaining.</strong></p>
      <a href="${process.env.APP_URL}/upgrade" class="btn">View Upgrade Options →</a>`)));
  }
  const sources = sourceIds?.length ? db.prepare(`SELECT * FROM knowledge_sources WHERE org_id=? AND id IN (${sourceIds.map(()=>'?').join(',')}) AND status=?`).all(req.user.org_id,...sourceIds,'connected') : db.prepare('SELECT * FROM knowledge_sources WHERE org_id=? AND status=?').all(req.user.org_id,'connected');
  res.status(201).json({ runId, sources });
});

app.post('/api/questionnaires/runs/:runId/questions', authenticate, (req,res) => {
  const { questionNum, questionText, category, questionType, answer, confidence, reasoning, sources, improvements } = req.body;
  const run = db.prepare('SELECT id FROM questionnaire_runs WHERE id=? AND org_id=?').get(req.params.runId,req.user.org_id);
  if (!run) return res.status(404).json({ error:'Run not found' });
  const id = uuid();
  db.prepare(`INSERT OR REPLACE INTO questions (id,run_id,org_id,question_num,question_text,category,question_type,answer,confidence,reasoning,sources,improvements)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`)
    .run(id,req.params.runId,req.user.org_id,questionNum,questionText,category,questionType,answer,confidence,reasoning,JSON.stringify(sources||[]),JSON.stringify(improvements||[]));
  res.status(201).json({ id });
});

app.patch('/api/questionnaires/runs/:runId/complete', authenticate, (req,res) => {
  const stats = db.prepare(`SELECT COUNT(*) as total,
    COUNT(CASE WHEN confidence>=75 THEN 1 END) as high,
    COUNT(CASE WHEN confidence>=50 AND confidence<75 THEN 1 END) as med,
    COUNT(CASE WHEN confidence<50 THEN 1 END) as low,
    AVG(confidence) as avg_conf
    FROM questions WHERE run_id=?`).get(req.params.runId);
  db.prepare(`UPDATE questionnaire_runs SET status='completed',total_questions=?,answered=?,
    high_confidence=?,med_confidence=?,low_confidence=?,avg_confidence=?,completed_at=datetime('now')
    WHERE id=? AND org_id=?`)
    .run(stats.total,stats.total,stats.high,stats.med,stats.low,Math.round(stats.avg_conf||0),req.params.runId,req.user.org_id);
  res.json({ message:'Run completed' });
});

app.get('/api/questionnaires/runs/:runId', authenticate, (req,res) => {
  const run = db.prepare('SELECT * FROM questionnaire_runs WHERE id=? AND org_id=?').get(req.params.runId,req.user.org_id);
  if (!run) return res.status(404).json({ error:'Run not found' });
  const questions = db.prepare('SELECT * FROM questions WHERE run_id=? ORDER BY question_num').all(req.params.runId);
  res.json({ run, questions:questions.map(q=>({...q,sources:JSON.parse(q.sources||'[]'),improvements:JSON.parse(q.improvements||'[]')})) });
});

app.patch('/api/questionnaires/questions/:id', authenticate, (req,res) => {
  const { answer, reviewStatus } = req.body;
  db.prepare('UPDATE questions SET answer=COALESCE(?,answer),review_status=COALESCE(?,review_status),reviewed_at=datetime("now") WHERE id=? AND org_id=?')
    .run(answer,reviewStatus,req.params.id,req.user.org_id);
  res.json({ message:'Updated' });
});

// ══════════════════════════════════════════════════════════
// SUPPLIERS
// ══════════════════════════════════════════════════════════

app.get('/api/suppliers', authenticate, (req,res) => {
  const suppliers = db.prepare(`SELECT s.*,COUNT(sa.id) as assessment_count
    FROM suppliers s LEFT JOIN supplier_assessments sa ON sa.supplier_id=s.id AND sa.status='completed'
    WHERE s.org_id=? GROUP BY s.id ORDER BY s.name`).all(req.user.org_id);
  res.json(suppliers);
});

app.post('/api/suppliers', authenticate, role('superadmin','org_admin','manager'), (req,res) => {
  const { name, domain, contactName, contactEmail, category, criticality, notes } = req.body;
  if (!name) return res.status(400).json({ error:'Supplier name required' });
  const id = uuid();
  db.prepare(`INSERT INTO suppliers (id,org_id,name,domain,contact_name,contact_email,category,criticality,notes) VALUES (?,?,?,?,?,?,?,?,?)`)
    .run(id,req.user.org_id,name,domain||'',contactName||'',contactEmail||'',category||'',criticality||'medium',notes||'');
  res.status(201).json(db.prepare('SELECT * FROM suppliers WHERE id=?').get(id));
});

app.patch('/api/suppliers/:id', authenticate, role('superadmin','org_admin','manager'), (req,res) => {
  const { name, domain, contactName, contactEmail, category, criticality, notes, status } = req.body;
  db.prepare(`UPDATE suppliers SET name=COALESCE(?,name),domain=COALESCE(?,domain),contact_name=COALESCE(?,contact_name),
    contact_email=COALESCE(?,contact_email),category=COALESCE(?,category),criticality=COALESCE(?,criticality),
    notes=COALESCE(?,notes),status=COALESCE(?,status),updated_at=datetime('now') WHERE id=? AND org_id=?`)
    .run(name,domain,contactName,contactEmail,category,criticality,notes,status,req.params.id,req.user.org_id);
  res.json({ message:'Updated' });
});

app.delete('/api/suppliers/:id', authenticate, role('superadmin','org_admin','manager'), (req,res) => {
  db.prepare('DELETE FROM suppliers WHERE id=? AND org_id=?').run(req.params.id,req.user.org_id);
  res.json({ message:'Deleted' });
});

// Supplier dashboard
app.get('/api/suppliers/dashboard', authenticate, (req,res) => {
  const counts = db.prepare(`SELECT COUNT(*) as total,
    COUNT(CASE WHEN risk_level='critical' THEN 1 END) as critical,
    COUNT(CASE WHEN risk_level='high' THEN 1 END) as high,
    COUNT(CASE WHEN risk_level='medium' THEN 1 END) as medium,
    COUNT(CASE WHEN risk_level='low' THEN 1 END) as low,
    COUNT(CASE WHEN risk_level='unknown' THEN 1 END) as unknown,
    AVG(overall_score) as avg_score
    FROM suppliers WHERE org_id=? AND status='active'`).get(req.user.org_id);
  const recent = db.prepare(`SELECT s.name,s.risk_level,s.overall_score,sa.completed_at,sa.score
    FROM suppliers s LEFT JOIN supplier_assessments sa ON sa.supplier_id=s.id AND sa.status='completed'
    WHERE s.org_id=? ORDER BY sa.completed_at DESC LIMIT 8`).all(req.user.org_id);
  res.json({ summary:counts, recentAssessments:recent });
});

// ══════════════════════════════════════════════════════════
// SUPPLIER ASSESSMENTS
// ══════════════════════════════════════════════════════════

app.post('/api/assessments/send', authenticate, role('superadmin','org_admin','manager'), async (req,res) => {
  try {
    const { supplierId, supplierEmail, supplierName, title } = req.body;
    if (!supplierEmail) return res.status(400).json({ error:'Supplier email required' });
    const token   = crypto.randomBytes(32).toString('hex');
    const expires = new Date(Date.now()+30*24*60*60*1000).toISOString();
    const id = uuid();
    db.prepare(`INSERT INTO supplier_assessments (id,org_id,supplier_id,sent_by,secure_token,token_expires,status,supplier_email,supplier_name)
      VALUES (?,?,?,?,?,?,?,?,?)`)
      .run(id,req.user.org_id,supplierId||null,req.user.id,token,expires,'sent',supplierEmail,supplierName||'');
    const org = db.prepare('SELECT name,logo_url,branding_name FROM organisations WHERE id=?').get(req.user.org_id);
    const assessUrl = `${process.env.APP_URL||'https://app.cybersure.co.uk'}/assess/${token}`;
    await sendEmail(supplierEmail,`Security Assessment Request from ${org?.name}`,emailWrap(`
      <div class="t">Security Assessment Request 📋</div>
      <p>Dear ${supplierName||'Supplier'},</p>
      <p><strong>${org?.name}</strong> has sent you a security questionnaire as part of their supplier due diligence.</p>
      <div class="box"><strong>${title||'Security Assessment'}</strong><br>
      <span style="color:#666;font-size:12px">Link expires: ${new Date(expires).toLocaleDateString('en-GB')}</span></div>
      <p>The assessment takes approximately 15–30 minutes. Your progress is saved automatically.</p>
      <a href="${assessUrl}" class="btn">Complete Assessment →</a>
      <div class="warn">⚠️ This link is unique to you — do not share it.</div>`,org?.name));
    res.status(201).json({ assessmentId:id, assessmentUrl:assessUrl });
  } catch(e) { console.error(e); res.status(500).json({ error:'Failed to send assessment' }); }
});

// Get assessment by token (public — supplier access)
app.get('/api/assessments/token/:token', (req,res) => {
  const a = db.prepare(`SELECT sa.*,o.name as org_name,o.logo_url,o.branding_name
    FROM supplier_assessments sa JOIN organisations o ON sa.org_id=o.id
    WHERE sa.secure_token=? AND sa.token_expires>datetime('now')`).get(req.params.token);
  if (!a) return res.status(404).json({ error:'Assessment link is invalid or has expired. Please contact the organisation that sent it.' });
  if (a.status==='pending'||a.status==='sent') {
    db.prepare('UPDATE supplier_assessments SET status=? WHERE id=?').run('in_progress',a.id);
  }
  res.json({ assessment:a, completed:a.status==='completed' });
});

// Submit assessment (public)
app.post('/api/assessments/token/:token/submit', async (req,res) => {
  try {
    const { answers } = req.body;
    if (!answers?.length) return res.status(400).json({ error:'Answers required' });
    const a = db.prepare(`SELECT * FROM supplier_assessments WHERE secure_token=? AND token_expires>datetime('now') AND status!='completed'`).get(req.params.token);
    if (!a) return res.status(404).json({ error:'Assessment not found or already completed' });
    const avg = Math.round(answers.reduce((s,q)=>s+(q.confidence||0),0)/answers.length);
    const riskLevel = avg>=75?'low':avg>=50?'medium':avg>=25?'high':'critical';
    const runId = uuid();
    db.prepare(`INSERT INTO questionnaire_runs (id,org_id,file_name,title,type,status,total_questions,answered,avg_confidence,completed_at)
      VALUES (?,?,'supplier_response.json',?,?,?,?,?,?,datetime('now'))`)
      .run(runId,a.org_id,`Supplier: ${a.supplier_name}`,'supplier_received','completed',answers.length,answers.length,avg);
    answers.forEach((q,i) => {
      db.prepare(`INSERT INTO questions (id,run_id,org_id,question_num,question_text,category,answer,confidence) VALUES (?,?,?,?,?,?,?,?)`)
        .run(uuid(),runId,a.org_id,i+1,q.question,q.category||'General',q.answer,q.confidence||0);
    });
    db.prepare(`UPDATE supplier_assessments SET status='completed',score=?,risk_level=?,completed_at=datetime('now'),run_id=? WHERE id=?`)
      .run(avg,riskLevel,runId,a.id);
    if (a.supplier_id) {
      db.prepare('UPDATE suppliers SET overall_score=?,risk_level=?,last_assessed=datetime("now") WHERE id=?').run(avg,riskLevel,a.supplier_id);
    }
    const admins = db.prepare('SELECT email,first_name FROM users WHERE org_id=? AND role IN (?,?)').all(a.org_id,'org_admin','manager');
    admins.forEach(admin => sendEmail(admin.email,`Assessment Complete — ${a.supplier_name}`,emailWrap(`
      <div class="t">Assessment Completed ✅</div>
      <p>Hi ${admin.first_name||'there'}, <strong>${a.supplier_name}</strong> has completed their security assessment.</p>
      <div class="box">
        <p><strong>Score:</strong> ${avg}%</p>
        <p><strong>Risk Level:</strong> <span style="font-weight:700;text-transform:capitalize;color:${avg>=75?'#006400':avg>=50?'#8B6914':'#CC0000'}">${riskLevel}</span></p>
      </div>
      <a href="${process.env.APP_URL}/suppliers" class="btn">View Full Results →</a>`)));
    res.json({ message:'Assessment submitted successfully', score:avg, riskLevel });
  } catch(e) { console.error(e); res.status(500).json({ error:'Failed to submit assessment' }); }
});

app.get('/api/assessments', authenticate, (req,res) => {
  const assessments = db.prepare(`SELECT sa.*,s.name as supplier_name,u.first_name,u.last_name
    FROM supplier_assessments sa LEFT JOIN suppliers s ON sa.supplier_id=s.id
    LEFT JOIN users u ON sa.sent_by=u.id WHERE sa.org_id=? ORDER BY sa.created_at DESC`).all(req.user.org_id);
  res.json(assessments);
});

// ══════════════════════════════════════════════════════════
// DASHBOARD
// ══════════════════════════════════════════════════════════

app.get('/api/dashboard', authenticate, (req,res) => {
  const org   = db.prepare('SELECT * FROM organisations WHERE id=?').get(req.user.org_id);
  const runs  = db.prepare(`SELECT COUNT(*) as total,AVG(avg_confidence) as avg_conf,
    COUNT(CASE WHEN created_at>datetime('now','-30 days') THEN 1 END) as this_month
    FROM questionnaire_runs WHERE org_id=?`).get(req.user.org_id);
  const supps = db.prepare(`SELECT COUNT(*) as total,
    COUNT(CASE WHEN risk_level='critical' THEN 1 END) as critical,
    COUNT(CASE WHEN risk_level='high' THEN 1 END) as high,
    COUNT(CASE WHEN risk_level='unknown' THEN 1 END) as unassessed
    FROM suppliers WHERE org_id=? AND status='active'`).get(req.user.org_id);
  const sources = db.prepare(`SELECT COUNT(*) as count FROM knowledge_sources WHERE org_id=? AND status='connected'`).get(req.user.org_id);
  const pending = db.prepare(`SELECT sa.*,s.name as supplier_name FROM supplier_assessments sa
    LEFT JOIN suppliers s ON sa.supplier_id=s.id
    WHERE sa.org_id=? AND sa.status IN ('sent','in_progress') ORDER BY sa.created_at DESC LIMIT 5`).all(req.user.org_id);
  res.json({
    organisation:{ name:org.name, plan:org.plan, status:org.status, logoUrl:org.logo_url, brandingName:org.branding_name },
    usage:{ questionnaires:{ used:org.questionnaires_used, limit:org.questionnaire_limit, pct:Math.round((org.questionnaires_used/Math.max(org.questionnaire_limit,1))*100) } },
    stats:{ totalRuns:runs.total, avgConfidence:Math.round(runs.avg_conf||0), thisMonth:runs.this_month,
      totalSuppliers:supps.total, criticalSuppliers:supps.critical, highRiskSuppliers:supps.high,
      unassessedSuppliers:supps.unassessed, knowledgeSources:sources.count },
    pendingAssessments:pending
  });
});

// ══════════════════════════════════════════════════════════
// WOOCOMMERCE WEBHOOK
// ══════════════════════════════════════════════════════════

app.post('/webhook/woocommerce', express.raw({ type:'*/*' }), async (req,res) => {
  try {
    const sig  = req.headers['x-wc-webhook-signature'];
    const body = req.body;
    if (process.env.WC_WEBHOOK_SECRET && sig) {
      const expected = crypto.createHmac('sha256',process.env.WC_WEBHOOK_SECRET).update(body).digest('base64');
      if (sig!==expected) return res.status(401).json({ error:'Invalid signature' });
    }
    const event = req.headers['x-wc-webhook-topic'];
    const data  = JSON.parse(body.toString());
    const limits = { starter:25, professional:100, enterprise:300, unlimited:999999 };
    const getPlan = d => (d.meta_data||d.line_items?.[0]?.meta_data||[]).find(m=>m.key==='questionnaire_plan')?.value||'starter';
    const custId = String(data.customer_id);

    if (event==='subscription.created'||event==='order.created') {
      const customerEmail = data.billing?.email;
      if (!customerEmail) return res.status(200).json({ received:true });
      const plan  = getPlan(data);
      const limit = limits[plan]||25;
      let org = db.prepare('SELECT * FROM organisations WHERE woo_customer_id=?').get(custId);
      if (!org) {
        const orgId = uuid();
        db.prepare(`INSERT INTO organisations (id,name,plan,questionnaire_limit,woo_customer_id,subscription_start,status)
          VALUES (?,?,?,?,?,datetime('now'),'active')`)
          .run(orgId,data.billing?.company||customerEmail,plan,limit,custId);
        const tempPw = crypto.randomBytes(8).toString('hex');
        const hash   = await bcrypt.hash(tempPw,12);
        db.prepare(`INSERT INTO users (id,org_id,email,password_hash,first_name,last_name,role,must_change_pw) VALUES (?,?,?,?,?,?,?,?)`)
          .run(uuid(),orgId,customerEmail,hash,data.billing?.first_name||'',data.billing?.last_name||'','org_admin',1);
        await sendEmail(customerEmail,'Welcome to CybeSure SecureAnswer',emailWrap(`
          <div class="t">Your Account is Ready 🛡</div>
          <p>Hi ${data.billing?.first_name||'there'}, thank you for subscribing to CybeSure SecureAnswer.</p>
          <div class="box">
            <p><strong>Login URL:</strong> <a href="${process.env.APP_URL}">${process.env.APP_URL}</a></p>
            <p><strong>Email:</strong> ${customerEmail}</p>
            <p><strong>Temporary password:</strong> <span class="code">${tempPw}</span></p>
            <p><strong>Plan:</strong> ${plan.charAt(0).toUpperCase()+plan.slice(1)} — ${limit} questionnaires/year</p>
          </div>
          <div class="warn">Change your password immediately on first login.</div>
          <a href="${process.env.APP_URL}" class="btn">Log In Now →</a>`));
      }
    }
    if (event==='subscription.updated') {
      const plan=getPlan(data); const limit=limits[plan]||25;
      db.prepare('UPDATE organisations SET plan=?,questionnaire_limit=?,updated_at=datetime("now") WHERE woo_customer_id=?').run(plan,limit,custId);
    }
    if (event==='subscription.renewed') {
      db.prepare('UPDATE organisations SET questionnaires_used=0,suppliers_used=0,subscription_start=datetime("now"),updated_at=datetime("now") WHERE woo_customer_id=?').run(custId);
    }
    if (event==='subscription.cancelled'||event==='subscription.on-hold') {
      const status = event==='subscription.cancelled'?'cancelled':'suspended';
      db.prepare('UPDATE organisations SET status=?,updated_at=datetime("now") WHERE woo_customer_id=?').run(status,custId);
    }
    res.status(200).json({ received:true });
  } catch(e) { console.error('Webhook error:',e); res.status(200).json({ received:true }); }
});

// ── SERVE FRONTEND ────────────────────────────────────────
app.get('*', (req,res) => {
  if (!req.path.startsWith('/api')&&!req.path.startsWith('/webhook')) {
    res.sendFile(path.join(__dirname,'public','index.html'));
  }
});

// ── START ─────────────────────────────────────────────────
app.listen(PORT, () => console.log(`\n🛡 CybeSure SecureAnswer running on port ${PORT}\n   Health: http://localhost:${PORT}/api/health\n`));
