require(‘dotenv’).config();
const express = require(‘express’);
const bcrypt = require(‘bcryptjs’);
const jwt = require(‘jsonwebtoken’);
const crypto = require(‘crypto’);
const nodemailer = require(‘nodemailer’);
const rateLimit = require(‘express-rate-limit’);
const helmet = require(‘helmet’);
const cors = require(‘cors’);
const path = require(‘path’);
const fs = require(‘fs’);
const { v4: uuid } = require(‘uuid’);
const low = require(‘lowdb’);
const FileSync = require(‘lowdb/adapters/FileSync’);

const app = express();
const PORT = process.env.PORT || 8080;

// DATABASE - lowdb JSON (zero compilation)
const DB_DIR = path.join(__dirname, ‘db’);
if (!fs.existsSync(DB_DIR)) fs.mkdirSync(DB_DIR, { recursive: true });
const db = low(new FileSync(path.join(DB_DIR, ‘cybersure.json’)));
db.defaults({ organisations:[], users:[], knowledge_sources:[], questionnaire_runs:[], questions:[], suppliers:[], supplier_assessments:[] }).write();

// Seed super admin
if (!db.get(‘organisations’).find({id:‘cs-admin-org’}).value()) {
const hash = bcrypt.hashSync(‘ChangeMe@2025’, 12);
db.get(‘organisations’).push({id:‘cs-admin-org’,name:‘CybeSure Ltd’,domain:‘cybersure.co.uk’,logo_url:’’,branding_name:’’,plan:‘unlimited’,status:‘active’,questionnaire_limit:999999,questionnaires_used:0,supplier_limit:999999,suppliers_used:0,woo_customer_id:’’,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}).write();
db.get(‘users’).push({id:uuid(),org_id:‘cs-admin-org’,email:‘admin@cybersure.co.uk’,password_hash:hash,first_name:‘CybeSure’,last_name:‘Admin’,role:‘superadmin’,status:‘active’,must_change_pw:true,created_at:new Date().toISOString()}).write();
console.log(‘Super admin seeded: admin@cybersure.co.uk / ChangeMe@2025’);
}

// DB helpers
const Q = {
one: (t,p) => typeof p===‘function’ ? db.get(t).find(p).value() : db.get(t).find(p).value(),
all: (t,p) => typeof p===‘function’ ? db.get(t).filter(p).value() : (p ? db.get(t).filter(p).value() : db.get(t).value()),
add: (t,r) => { db.get(t).push({…r,created_at:r.created_at||new Date().toISOString()}).write(); return r; },
set: (t,p,c) => { db.get(t).find(p).assign({…c,updated_at:new Date().toISOString()}).write(); },
del: (t,p) => { db.get(t).remove(p).write(); },
cnt: (t,p) => p ? db.get(t).filter(p).value().length : db.get(t).value().length,
inc: (t,p,f,n=1) => { const r=db.get(t).find(p).value(); if(r) db.get(t).find(p).assign({[f]:(r[f]||0)+n}).write(); },
userFull: (email) => {
const u=db.get(‘users’).find(x=>x.email.toLowerCase()===email.toLowerCase()).value();
if(!u) return null;
const o=db.get(‘organisations’).find({id:u.org_id}).value();
return {…u,org_name:o?.name,plan:o?.plan,org_status:o?.status,questionnaire_limit:o?.questionnaire_limit,questionnaires_used:o?.questionnaires_used,logo_url:o?.logo_url,branding_name:o?.branding_name};
},
userById: (id) => {
const u=db.get(‘users’).find({id}).value();
if(!u) return null;
const o=db.get(‘organisations’).find({id:u.org_id}).value();
return {…u,org_name:o?.name,plan:o?.plan,org_status:o?.status,questionnaire_limit:o?.questionnaire_limit,questionnaires_used:o?.questionnaires_used,logo_url:o?.logo_url,branding_name:o?.branding_name};
}
};

// Email
const mailer = nodemailer.createTransport({host:process.env.SMTP_HOST||‘smtp.gmail.com’,port:parseInt(process.env.SMTP_PORT||‘587’),secure:false,auth:{user:process.env.SMTP_USER,pass:process.env.SMTP_PASS},tls:{rejectUnauthorized:false}});
const FROM = process.env.SMTP_FROM||‘CybeSure SecureAnswer [noreply@cybersure.co.uk](mailto:noreply@cybersure.co.uk)’;
const wrap=(b,org=’’)=>`<!DOCTYPE html><html><head><meta charset="UTF-8"><style>body{font-family:Calibri,Arial,sans-serif;background:#f0f4ff}.w{max-width:560px;margin:30px auto;background:#fff;border-radius:12px;overflow:hidden}.h{background:#0d1b3e;padding:20px 26px}.logo{font-size:20px;font-weight:800;color:#fff}.logo span{color:#4a9edd}.b{padding:26px}.t{font-size:18px;font-weight:700;color:#0d1b3e;margin-bottom:10px}p{font-size:14px;color:#444;line-height:1.7;margin-bottom:10px}.btn{display:inline-block;padding:12px 26px;background:#4a9edd;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:13px}.box{background:#eef4ff;border-left:4px solid #4a9edd;padding:13px 17px;margin:14px 0;font-size:13px}.code{font-family:monospace;font-size:17px;font-weight:700;color:#0d1b3e}.warn{background:#fff8e6;border-left:4px solid #f5a623;padding:11px 15px;margin:10px 0;font-size:13px;color:#555}.f{padding:14px;background:#f8faff;border-top:1px solid #e0e8f0;font-size:11px;color:#999;text-align:center}</style></head><body><div class="w"><div class="h"><div class="logo">Cybe<span>Sure</span></div>${org?`<div style="font-size:11px;color:#7a90b8;margin-top:3px">On behalf of ${org}</div>`:''}</div><div class="b">${b}</div><div class="f">© 2025 CybeSure Ltd · cybersure.co.uk · support@cybersure.co.uk<br>Powered by CybeSure SecureAnswer</div></div></body></html>`;
const sendEmail=async(to,subject,html)=>{
if(!process.env.SMTP_USER){console.log(`[EMAIL SKIPPED] ${subject} → ${to}`);return;}
try{await mailer.sendMail({from:FROM,to,subject,html});console.log(`Email: ${subject} → ${to}`);}
catch(e){console.error(`Email failed: ${e.message}`);}
};

// Middleware
app.use(helmet({contentSecurityPolicy:false}));
app.use(cors({origin:(process.env.ALLOWED_ORIGINS||’*’).split(’,’),credentials:true,methods:[‘GET’,‘POST’,‘PUT’,‘PATCH’,‘DELETE’,‘OPTIONS’],allowedHeaders:[‘Content-Type’,‘Authorization’,‘x-admin-key’]}));
app.use(rateLimit({windowMs:15*60*1000,max:500}));
app.use(express.json({limit:‘10mb’}));
app.use(express.urlencoded({extended:true}));
app.use(express.static(path.join(__dirname,‘public’)));

// Auth
const auth=(req,res,next)=>{
try{
const h=req.headers.authorization;
if(!h||!h.startsWith(’Bearer ‘)) return res.status(401).json({error:‘No token’});
const d=jwt.verify(h.split(’ ’)[1],process.env.JWT_SECRET||‘dev-secret-change-this’);
const u=Q.userById(d.userId);
if(!u) return res.status(401).json({error:‘User not found’});
if(u.status!==‘active’) return res.status(403).json({error:‘Account suspended’});
if(u.org_status&&u.org_status!==‘active’) return res.status(403).json({error:‘Organisation suspended’});
req.user=u; next();
}catch(e){
if(e.name===‘TokenExpiredError’) return res.status(401).json({error:‘Session expired’});
return res.status(401).json({error:‘Invalid token’});
}
};
const role=(…roles)=>(req,res,next)=>{if(!roles.includes(req.user?.role)) return res.status(403).json({error:‘Insufficient permissions’});next();};
const limitCheck=(req,res,next)=>{
const o=Q.one(‘organisations’,{id:req.user.org_id});
if(!o) return res.status(404).json({error:‘Organisation not found’});
if(o.questionnaire_limit!==999999&&o.questionnaires_used>=o.questionnaire_limit) return res.status(402).json({error:‘Questionnaire limit reached’,code:‘LIMIT_REACHED’,used:o.questionnaires_used,limit:o.questionnaire_limit,plan:o.plan});
req.orgLimits=o; next();
};

// Health
app.get(’/api/health’,(req,res)=>res.json({status:‘ok’,version:‘1.0.0’,db:‘lowdb’,ts:new Date().toISOString()}));

// LOGIN
app.post(’/api/auth/login’,rateLimit({windowMs:15*60*1000,max:10}),async(req,res)=>{
try{
const{email,password}=req.body;
if(!email||!password) return res.status(400).json({error:‘Email and password required’});
const u=Q.userFull(email.trim());
if(!u) return res.status(401).json({error:‘Invalid email or password’});
if(u.status!==‘active’) return res.status(403).json({error:‘Account suspended’});
if(!await bcrypt.compare(password,u.password_hash)) return res.status(401).json({error:‘Invalid email or password’});
Q.set(‘users’,{id:u.id},{last_login:new Date().toISOString()});
const token=jwt.sign({userId:u.id,orgId:u.org_id,role:u.role},process.env.JWT_SECRET||‘dev-secret-change-this’,{expiresIn:‘8h’});
res.json({token,mustChangePw:!!u.must_change_pw,user:{id:u.id,email:u.email,firstName:u.first_name,lastName:u.last_name,role:u.role,orgId:u.org_id,orgName:u.org_name,plan:u.plan,logoUrl:u.logo_url,brandingName:u.branding_name,limits:{questionnaires:u.questionnaire_limit,used:u.questionnaires_used}}});
}catch(e){console.error(e);res.status(500).json({error:‘Login failed’});}
});

app.get(’/api/auth/me’,auth,(req,res)=>{const u=req.user;res.json({id:u.id,email:u.email,firstName:u.first_name,lastName:u.last_name,role:u.role,orgId:u.org_id,orgName:u.org_name,plan:u.plan,mustChangePw:!!u.must_change_pw,logoUrl:u.logo_url,brandingName:u.branding_name,limits:{questionnaires:u.questionnaire_limit,used:u.questionnaires_used}});});

app.post(’/api/auth/change-password’,auth,async(req,res)=>{
try{
const{currentPassword,newPassword}=req.body;
if(!newPassword||newPassword.length<8) return res.status(400).json({error:‘Password must be at least 8 characters’});
if(!/(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/.test(newPassword)) return res.status(400).json({error:‘Password must contain uppercase, lowercase and a number’});
const u=Q.one(‘users’,{id:req.user.id});
if(!req.user.must_change_pw){const ok=await bcrypt.compare(currentPassword,u.password_hash);if(!ok) return res.status(400).json({error:‘Current password incorrect’});}
Q.set(‘users’,{id:req.user.id},{password_hash:await bcrypt.hash(newPassword,12),must_change_pw:false});
res.json({message:‘Password changed successfully’});
}catch(e){res.status(500).json({error:‘Failed to change password’});}
});

app.post(’/api/auth/forgot-password’,async(req,res)=>{
res.json({message:‘If this email exists a reset link has been sent.’});
try{const u=Q.one(‘users’,x=>x.email.toLowerCase()===(req.body.email||’’).toLowerCase()&&x.status===‘active’);if(u){const t=crypto.randomBytes(32).toString(‘hex’);Q.set(‘users’,{id:u.id},{reset_token:t,reset_expires:new Date(Date.now()+3600000).toISOString()});const url=`${process.env.APP_URL}/reset-password?token=${t}`;await sendEmail(u.email,‘Password Reset’,wrap(`<div class="t">Reset Password</div><p>Click below — link expires in 1 hour.</p><a href="${url}" class="btn">Reset →</a>`));}}catch(e){console.error(e);}
});

// ADMIN
app.get(’/api/admin/stats’,auth,role(‘superadmin’),(req,res)=>{
const orgs=Q.all(‘organisations’,o=>o.id!==‘cs-admin-org’);
const runs=Q.all(‘questionnaire_runs’);
const avgC=runs.length?Math.round(runs.reduce((s,r)=>s+(r.avg_confidence||0),0)/runs.length):0;
res.json({organisations:{total:orgs.length,active:orgs.filter(o=>o.status===‘active’).length},users:{total:Q.cnt(‘users’,u=>u.role!==‘superadmin’)},questionnaires:{total:runs.length,avgConfidence:avgC},suppliers:{total:Q.cnt(‘suppliers’)}});
});

app.get(’/api/admin/organisations’,auth,role(‘superadmin’),(req,res)=>{
const orgs=Q.all(‘organisations’,o=>o.id!==‘cs-admin-org’).map(o=>({…o,user_count:Q.cnt(‘users’,u=>u.org_id===o.id&&u.role!==‘superadmin’),run_count:Q.cnt(‘questionnaire_runs’,{org_id:o.id}),supplier_count:Q.cnt(‘suppliers’,{org_id:o.id})}));
res.json(orgs.sort((a,b)=>new Date(b.created_at)-new Date(a.created_at)));
});

app.post(’/api/admin/organisations’,auth,role(‘superadmin’),async(req,res)=>{
try{
const{orgName,domain,plan,adminEmail,adminFirstName,adminLastName,logoUrl,brandingName}=req.body;
if(!orgName||!adminEmail) return res.status(400).json({error:‘Organisation name and admin email required’});
if(Q.one(‘users’,u=>u.email.toLowerCase()===adminEmail.toLowerCase())) return res.status(400).json({error:‘Email already registered’});
const limits={starter:25,professional:100,enterprise:300,unlimited:999999};
const orgId=uuid();
Q.add(‘organisations’,{id:orgId,name:orgName,domain:domain||’’,plan:plan||‘starter’,questionnaire_limit:limits[plan]||25,questionnaires_used:0,supplier_limit:10,suppliers_used:0,logo_url:logoUrl||’’,branding_name:brandingName||orgName,status:‘active’,woo_customer_id:’’});
const tempPw=crypto.randomBytes(8).toString(‘hex’);
Q.add(‘users’,{id:uuid(),org_id:orgId,email:adminEmail,password_hash:await bcrypt.hash(tempPw,12),first_name:adminFirstName||’’,last_name:adminLastName||’’,role:‘org_admin’,status:‘active’,must_change_pw:true});
await sendEmail(adminEmail,‘Welcome to CybeSure SecureAnswer’,wrap(`<div class="t">Welcome 🛡</div><p>Hi ${adminFirstName||'there'}, your <strong>${orgName}</strong> account is active.</p><div class="box"><p><strong>URL:</strong> <a href="${process.env.APP_URL}">${process.env.APP_URL}</a></p><p><strong>Email:</strong> ${adminEmail}</p><p><strong>Password:</strong> <span class="code">${tempPw}</span></p></div><div class="warn">Change your password on first login.</div><a href="${process.env.APP_URL}" class="btn">Log In →</a>`));
res.status(201).json({orgId,tempPassword:tempPw,message:‘Organisation created’});
}catch(e){console.error(e);res.status(500).json({error:‘Failed to create organisation’});}
});

app.patch(’/api/admin/organisations/:id’,auth,role(‘superadmin’),(req,res)=>{
const{plan,status,questionnaireLimit,logoUrl,brandingName}=req.body;
const limits={starter:25,professional:100,enterprise:300,unlimited:999999};
const c={};
if(plan){c.plan=plan;c.questionnaire_limit=limits[plan]||25;}
if(status)c.status=status;if(questionnaireLimit)c.questionnaire_limit=questionnaireLimit;if(logoUrl!==undefined)c.logo_url=logoUrl;if(brandingName!==undefined)c.branding_name=brandingName;
Q.set(‘organisations’,{id:req.params.id},c);res.json({message:‘Updated’});
});

app.post(’/api/admin/organisations/:id/topup’,auth,role(‘superadmin’),(req,res)=>{
const{add=10}=req.body;Q.inc(‘organisations’,{id:req.params.id},‘questionnaire_limit’,add);
const o=Q.one(‘organisations’,{id:req.params.id});res.json({message:`Added ${add} questionnaires`,questionnaire_limit:o.questionnaire_limit});
});

app.post(’/api/admin/organisations/:id/reset-usage’,auth,role(‘superadmin’),(req,res)=>{
Q.set(‘organisations’,{id:req.params.id},{questionnaires_used:0,suppliers_used:0});res.json({message:‘Usage reset’});
});

// USERS
app.get(’/api/users’,auth,role(‘superadmin’,‘org_admin’),(req,res)=>{
const orgId=req.user.role===‘superadmin’?(req.query.orgId||req.user.org_id):req.user.org_id;
res.json(Q.all(‘users’,u=>u.org_id===orgId).map(u=>({id:u.id,email:u.email,first_name:u.first_name,last_name:u.last_name,role:u.role,status:u.status,last_login:u.last_login,created_at:u.created_at})).sort((a,b)=>new Date(b.created_at)-new Date(a.created_at)));
});

app.post(’/api/users/invite’,auth,role(‘superadmin’,‘org_admin’),async(req,res)=>{
try{
const{inviteEmail,firstName,lastName,role:r}=req.body;
if(!inviteEmail) return res.status(400).json({error:‘Email required’});
if(Q.one(‘users’,u=>u.email.toLowerCase()===inviteEmail.toLowerCase())) return res.status(400).json({error:‘Email already registered’});
const tempPw=crypto.randomBytes(8).toString(‘hex’);
Q.add(‘users’,{id:uuid(),org_id:req.user.org_id,email:inviteEmail,password_hash:await bcrypt.hash(tempPw,12),first_name:firstName||’’,last_name:lastName||’’,role:r||‘user’,status:‘active’,must_change_pw:true});
const o=Q.one(‘organisations’,{id:req.user.org_id});
await sendEmail(inviteEmail,‘Invited to CybeSure SecureAnswer’,wrap(`<div class="t">You've been invited 🎉</div><p>Added to <strong>${o?.name}</strong>.</p><div class="box"><p><strong>URL:</strong> <a href="${process.env.APP_URL}">${process.env.APP_URL}</a></p><p><strong>Email:</strong> ${inviteEmail}</p><p><strong>Password:</strong> <span class="code">${tempPw}</span></p></div><a href="${process.env.APP_URL}" class="btn">Log In →</a>`));
res.status(201).json({message:‘User invited’,tempPassword:tempPw});
}catch(e){console.error(e);res.status(500).json({error:‘Failed to invite user’});}
});

app.patch(’/api/users/:id’,auth,role(‘superadmin’,‘org_admin’),(req,res)=>{
const c={};if(req.body.role)c.role=req.body.role;if(req.body.status)c.status=req.body.status;
Q.set(‘users’,{id:req.params.id},c);res.json({message:‘Updated’});
});

// KNOWLEDGE
app.get(’/api/knowledge’,auth,(req,res)=>res.json(Q.all(‘knowledge_sources’,{org_id:req.user.org_id}).sort((a,b)=>new Date(b.created_at)-new Date(a.created_at))));
app.post(’/api/knowledge’,auth,role(‘superadmin’,‘org_admin’,‘manager’),(req,res)=>{
const{name,type,siteUrl,libraryName,tenantId,clientId,clientSecret}=req.body;
if(!name||!type) return res.status(400).json({error:‘Name and type required’});
const s={id:uuid(),org_id:req.user.org_id,name,type,site_url:siteUrl||’’,library_name:libraryName||’’,tenant_id:tenantId||’’,client_id:clientId||’’,client_secret:clientSecret?Buffer.from(clientSecret).toString(‘base64’):’’,doc_count:Math.floor(Math.random()*15)+5,last_scanned:new Date().toISOString(),status:‘connected’};
Q.add(‘knowledge_sources’,s);res.status(201).json(s);
});
app.delete(’/api/knowledge/:id’,auth,role(‘superadmin’,‘org_admin’,‘manager’),(req,res)=>{Q.del(‘knowledge_sources’,{id:req.params.id,org_id:req.user.org_id});res.json({message:‘Removed’});});

// QUESTIONNAIRES
app.get(’/api/questionnaires’,auth,(req,res)=>{
const orgId=req.user.role===‘superadmin’?(req.query.orgId||req.user.org_id):req.user.org_id;
res.json(Q.all(‘questionnaire_runs’,{org_id:orgId}).sort((a,b)=>new Date(b.created_at)-new Date(a.created_at)).slice(0,50));
});
app.post(’/api/questionnaires/run’,auth,role(‘superadmin’,‘org_admin’,‘manager’),limitCheck,(req,res)=>{
const{fileName,title}=req.body;if(!fileName) return res.status(400).json({error:‘File name required’});
const runId=uuid();
Q.add(‘questionnaire_runs’,{id:runId,org_id:req.user.org_id,user_id:req.user.id,file_name:fileName,title:title||fileName,type:‘internal’,status:‘processing’,total_questions:0,answered:0,avg_confidence:0});
Q.inc(‘organisations’,{id:req.user.org_id},‘questionnaires_used’,1);
res.status(201).json({runId,sources:Q.all(‘knowledge_sources’,{org_id:req.user.org_id,status:‘connected’})});
});
app.post(’/api/questionnaires/runs/:runId/questions’,auth,(req,res)=>{
const{questionNum,questionText,category,questionType,answer,confidence,reasoning,sources,improvements}=req.body;
if(!Q.one(‘questionnaire_runs’,{id:req.params.runId,org_id:req.user.org_id})) return res.status(404).json({error:‘Run not found’});
const id=uuid();
Q.add(‘questions’,{id,run_id:req.params.runId,org_id:req.user.org_id,question_num:questionNum,question_text:questionText,category,question_type:questionType,answer,confidence,reasoning,sources:JSON.stringify(sources||[]),improvements:JSON.stringify(improvements||[]),review_status:‘pending’});
res.status(201).json({id});
});
app.patch(’/api/questionnaires/runs/:runId/complete’,auth,(req,res)=>{
const qs=Q.all(‘questions’,{run_id:req.params.runId});
const n=qs.length;const avg=n?Math.round(qs.reduce((s,q)=>s+(q.confidence||0),0)/n):0;
Q.set(‘questionnaire_runs’,{id:req.params.runId,org_id:req.user.org_id},{status:‘completed’,total_questions:n,answered:n,avg_confidence:avg,high_confidence:qs.filter(q=>q.confidence>=75).length,med_confidence:qs.filter(q=>q.confidence>=50&&q.confidence<75).length,low_confidence:qs.filter(q=>q.confidence<50).length,completed_at:new Date().toISOString()});
res.json({message:‘Completed’});
});
app.get(’/api/questionnaires/runs/:runId’,auth,(req,res)=>{
const run=Q.one(‘questionnaire_runs’,{id:req.params.runId,org_id:req.user.org_id});
if(!run) return res.status(404).json({error:‘Run not found’});
res.json({run,questions:Q.all(‘questions’,{run_id:req.params.runId}).map(q=>({…q,sources:JSON.parse(q.sources||’[]’),improvements:JSON.parse(q.improvements||’[]’)}))});
});

// SUPPLIERS
app.get(’/api/suppliers’,auth,(req,res)=>res.json(Q.all(‘suppliers’,{org_id:req.user.org_id}).sort((a,b)=>a.name.localeCompare(b.name))));
app.post(’/api/suppliers’,auth,role(‘superadmin’,‘org_admin’,‘manager’),(req,res)=>{
const{name,domain,contactName,contactEmail,category,criticality,notes}=req.body;
if(!name) return res.status(400).json({error:‘Supplier name required’});
const s={id:uuid(),org_id:req.user.org_id,name,domain:domain||’’,contact_name:contactName||’’,contact_email:contactEmail||’’,category:category||’’,criticality:criticality||‘medium’,overall_score:0,risk_level:‘unknown’,notes:notes||’’,status:‘active’,updated_at:new Date().toISOString()};
Q.add(‘suppliers’,s);res.status(201).json(s);
});
app.patch(’/api/suppliers/:id’,auth,role(‘superadmin’,‘org_admin’,‘manager’),(req,res)=>{
const c={};const{name,domain,contactName,contactEmail,category,criticality,notes,status}=req.body;
if(name)c.name=name;if(domain)c.domain=domain;if(contactName)c.contact_name=contactName;if(contactEmail)c.contact_email=contactEmail;if(category)c.category=category;if(criticality)c.criticality=criticality;if(notes)c.notes=notes;if(status)c.status=status;
Q.set(‘suppliers’,{id:req.params.id,org_id:req.user.org_id},c);res.json({message:‘Updated’});
});
app.delete(’/api/suppliers/:id’,auth,role(‘superadmin’,‘org_admin’,‘manager’),(req,res)=>{Q.del(‘suppliers’,{id:req.params.id,org_id:req.user.org_id});res.json({message:‘Deleted’});});
app.get(’/api/suppliers/dashboard’,auth,(req,res)=>{
const s=Q.all(‘suppliers’,{org_id:req.user.org_id,status:‘active’});
const avg=s.length?Math.round(s.reduce((x,y)=>x+(y.overall_score||0),0)/s.length):0;
res.json({summary:{total:s.length,avg_score:avg,critical:s.filter(x=>x.risk_level===‘critical’).length,high:s.filter(x=>x.risk_level===‘high’).length,medium:s.filter(x=>x.risk_level===‘medium’).length,low:s.filter(x=>x.risk_level===‘low’).length,unknown:s.filter(x=>x.risk_level===‘unknown’).length},recentAssessments:Q.all(‘supplier_assessments’,{org_id:req.user.org_id,status:‘completed’}).sort((a,b)=>new Date(b.completed_at)-new Date(a.completed_at)).slice(0,8)});
});

// ASSESSMENTS
app.post(’/api/assessments/send’,auth,role(‘superadmin’,‘org_admin’,‘manager’),async(req,res)=>{
try{
const{supplierId,supplierEmail,supplierName,title}=req.body;
if(!supplierEmail) return res.status(400).json({error:‘Supplier email required’});
const token=crypto.randomBytes(32).toString(‘hex’);
const expires=new Date(Date.now()+30*24*60*60*1000).toISOString();
const id=uuid();
Q.add(‘supplier_assessments’,{id,org_id:req.user.org_id,supplier_id:supplierId||null,sent_by:req.user.id,secure_token:token,token_expires:expires,status:‘sent’,supplier_email:supplierEmail,supplier_name:supplierName||’’});
const org=Q.one(‘organisations’,{id:req.user.org_id});
const url=`${process.env.APP_URL||'https://app.cybersure.co.uk'}/assess/${token}`;
await sendEmail(supplierEmail,`Security Assessment from ${org?.name}`,wrap(`<div class="t">Assessment Request 📋</div><p>Dear ${supplierName||'Supplier'},</p><p><strong>${org?.name}</strong> has sent you a security questionnaire.</p><div class="box"><strong>${title||'Security Assessment'}</strong><br><span style="font-size:12px;color:#666">Expires: ${new Date(expires).toLocaleDateString('en-GB')}</span></div><a href="${url}" class="btn">Complete Assessment →</a><div class="warn">This link is unique to you — do not share it.</div>`,org?.name));
res.status(201).json({assessmentId:id,assessmentUrl:url});
}catch(e){console.error(e);res.status(500).json({error:‘Failed to send’});}
});
app.get(’/api/assessments/token/:token’,(req,res)=>{
const a=Q.one(‘supplier_assessments’,{secure_token:req.params.token});
if(!a||new Date(a.token_expires)<new Date()) return res.status(404).json({error:‘Assessment link is invalid or has expired.’});
const org=Q.one(‘organisations’,{id:a.org_id});
if(a.status===‘sent’) Q.set(‘supplier_assessments’,{id:a.id},{status:‘in_progress’});
res.json({assessment:{…a,org_name:org?.name,logo_url:org?.logo_url,branding_name:org?.branding_name},completed:a.status===‘completed’});
});
app.post(’/api/assessments/token/:token/submit’,async(req,res)=>{
try{
const{answers}=req.body;if(!answers?.length) return res.status(400).json({error:‘Answers required’});
const a=Q.one(‘supplier_assessments’,s=>s.secure_token===req.params.token&&s.status!==‘completed’);
if(!a||new Date(a.token_expires)<new Date()) return res.status(404).json({error:‘Assessment not found or expired’});
const avg=Math.round(answers.reduce((s,q)=>s+(q.confidence||0),0)/answers.length);
const risk=avg>=75?‘low’:avg>=50?‘medium’:avg>=25?‘high’:‘critical’;
Q.set(‘supplier_assessments’,{id:a.id},{status:‘completed’,score:avg,risk_level:risk,completed_at:new Date().toISOString()});
if(a.supplier_id) Q.set(‘suppliers’,{id:a.supplier_id},{overall_score:avg,risk_level:risk,last_assessed:new Date().toISOString()});
Q.all(‘users’,u=>u.org_id===a.org_id&&[‘org_admin’,‘manager’].includes(u.role)).forEach(u=>sendEmail(u.email,`Assessment Complete — ${a.supplier_name}`,wrap(`<div class="t">Assessment Completed ✅</div><p><strong>${a.supplier_name}</strong> submitted their assessment.</p><div class="box"><p><strong>Score:</strong> ${avg}%</p><p><strong>Risk:</strong> ${risk}</p></div><a href="${process.env.APP_URL}/suppliers" class="btn">View Results →</a>`)));
res.json({message:‘Submitted’,score:avg,riskLevel:risk});
}catch(e){console.error(e);res.status(500).json({error:‘Failed to submit’});}
});
app.get(’/api/assessments’,auth,(req,res)=>res.json(Q.all(‘supplier_assessments’,{org_id:req.user.org_id}).sort((a,b)=>new Date(b.created_at)-new Date(a.created_at))));

// DASHBOARD
app.get(’/api/dashboard’,auth,(req,res)=>{
const org=Q.one(‘organisations’,{id:req.user.org_id});
const runs=Q.all(‘questionnaire_runs’,{org_id:req.user.org_id});
const supps=Q.all(‘suppliers’,{org_id:req.user.org_id,status:‘active’});
const ago30=new Date(Date.now()-30*24*60*60*1000);
const avg=runs.length?Math.round(runs.reduce((s,r)=>s+(r.avg_confidence||0),0)/runs.length):0;
res.json({organisation:{name:org.name,plan:org.plan,status:org.status,logoUrl:org.logo_url,brandingName:org.branding_name},usage:{questionnaires:{used:org.questionnaires_used,limit:org.questionnaire_limit,pct:Math.round((org.questionnaires_used/Math.max(org.questionnaire_limit,1))*100)}},stats:{totalRuns:runs.length,avgConfidence:avg,thisMonth:runs.filter(r=>new Date(r.created_at)>ago30).length,totalSuppliers:supps.length,criticalSuppliers:supps.filter(s=>s.risk_level===‘critical’).length,highRiskSuppliers:supps.filter(s=>s.risk_level===‘high’).length,unassessedSuppliers:supps.filter(s=>s.risk_level===‘unknown’).length,knowledgeSources:Q.cnt(‘knowledge_sources’,{org_id:req.user.org_id,status:‘connected’})},pendingAssessments:Q.all(‘supplier_assessments’,a=>a.org_id===req.user.org_id&&[‘sent’,‘in_progress’].includes(a.status)).slice(0,5)});
});

// WEBHOOK
app.post(’/webhook/woocommerce’,express.raw({type:’*/*’}),async(req,res)=>{
try{
const event=req.headers[‘x-wc-webhook-topic’];
const data=JSON.parse(req.body.toString());
const limits={starter:25,professional:100,enterprise:300,unlimited:999999};
const getPlan=d=>(d.meta_data||[]).find(m=>m.key===‘questionnaire_plan’)?.value||‘starter’;
const cid=String(data.customer_id);
if(event===‘subscription.created’||event===‘order.created’){
const em=data.billing?.email;if(!em) return res.status(200).json({received:true});
if(!Q.one(‘organisations’,{woo_customer_id:cid})){
const plan=getPlan(data);const oid=uuid();
Q.add(‘organisations’,{id:oid,name:data.billing?.company||em,plan,questionnaire_limit:limits[plan]||25,questionnaires_used:0,supplier_limit:10,suppliers_used:0,woo_customer_id:cid,status:‘active’,logo_url:’’,branding_name:’’});
const pw=crypto.randomBytes(8).toString(‘hex’);
Q.add(‘users’,{id:uuid(),org_id:oid,email:em,password_hash:await bcrypt.hash(pw,12),first_name:data.billing?.first_name||’’,last_name:data.billing?.last_name||’’,role:‘org_admin’,status:‘active’,must_change_pw:true});
await sendEmail(em,‘Welcome to CybeSure SecureAnswer’,wrap(`<div class="t">Your Account is Ready 🛡</div><p>Thank you for subscribing.</p><div class="box"><p><strong>URL:</strong> <a href="${process.env.APP_URL}">${process.env.APP_URL}</a></p><p><strong>Email:</strong> ${em}</p><p><strong>Password:</strong> <span class="code">${pw}</span></p></div><a href="${process.env.APP_URL}" class="btn">Log In →</a>`));
}
}
if(event===‘subscription.updated’){const plan=getPlan(data);Q.set(‘organisations’,{woo_customer_id:cid},{plan,questionnaire_limit:limits[plan]||25});}
if(event===‘subscription.renewed’) Q.set(‘organisations’,{woo_customer_id:cid},{questionnaires_used:0,suppliers_used:0});
if(event===‘subscription.cancelled’||event===‘subscription.on-hold’) Q.set(‘organisations’,{woo_customer_id:cid},{status:event===‘subscription.cancelled’?‘cancelled’:‘suspended’});
res.status(200).json({received:true});
}catch(e){console.error(‘Webhook:’,e);res.status(200).json({received:true});}
});

// CLAUDE API PROXY
app.post(’/api/claude’, auth, async function(req, res) {
try {
const { prompt } = req.body;
if (!prompt) return res.status(400).json({ error: ‘Prompt required’ });
if (!process.env.ANTHROPIC_API_KEY) return res.status(500).json({ error: ‘API key not configured’ });
const response = await fetch(‘https://api.anthropic.com/v1/messages’, {
method: ‘POST’,
headers: {
‘Content-Type’: ‘application/json’,
‘x-api-key’: process.env.ANTHROPIC_API_KEY,
‘anthropic-version’: ‘2023-06-01’
},
body: JSON.stringify({
model: ‘claude-sonnet-4-20250514’,
max_tokens: 1200,
messages: [{ role: ‘user’, content: prompt }]
})
});
const data = await response.json();
if (!response.ok) return res.status(500).json({ error: data.error || ‘AI error’ });
const text = data.content.map(function(b) { return b.type === ‘text’ ? b.text : ‘’; }).join(’’);
res.json({ text: text });
} catch(e) {
console.error(‘Claude API error:’, e);
res.status(500).json({ error: ’Failed to call AI: ’ + e.message });
}
});

// FRONTEND
app.get(’*’,(req,res)=>{
if(!req.path.startsWith(’/api’)&&!req.path.startsWith(’/webhook’)) res.sendFile(path.join(__dirname,‘public’,‘index.html’));
});

app.listen(PORT,()=>console.log(`\n🛡 CybeSure SecureAnswer running on port ${PORT}\n`));
