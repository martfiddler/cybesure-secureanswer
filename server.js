require('dotenv').config();
const express = require('express');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const nodemailer = require('nodemailer');
const rateLimit = require('express-rate-limit');
const helmet = require('helmet');
const cors = require('cors');
const path = require('path');
const fs = require('fs');
const { v4: uuid } = require('uuid');
const low = require('lowdb');
const FileSync = require('lowdb/adapters/FileSync');

const app = express();
const PORT = process.env.PORT || 8080;

const DB_DIR = path.join(__dirname, 'db');
if (!fs.existsSync(DB_DIR)) fs.mkdirSync(DB_DIR, { recursive: true });
const db = low(new FileSync(path.join(DB_DIR, 'cybersure.json')));
db.defaults({ organisations:[], users:[], knowledge_sources:[], questionnaire_runs:[], questions:[], suppliers:[], supplier_assessments:[] }).write();

if (!db.get('organisations').find({id:'cs-admin-org'}).value()) {
const hash = bcrypt.hashSync('ChangeMe@2025', 12);
db.get('organisations').push({id:'cs-admin-org',name:'CybeSure Ltd',domain:'cybersure.co.uk',logo_url:'',branding_name:'',plan:'unlimited',status:'active',questionnaire_limit:999999,questionnaires_used:0,supplier_limit:999999,suppliers_used:0,woo_customer_id:'',created_at:new Date().toISOString(),updated_at:new Date().toISOString()}).write();
db.get('users').push({id:uuid(),org_id:'cs-admin-org',email:'admin@cybersure.co.uk',password_hash:hash,first_name:'CybeSure',last_name:'Admin',role:'superadmin',status:'active',must_change_pw:true,created_at:new Date().toISOString()}).write();
console.log('Super admin seeded');
}

const Q = {
one: (t,p) => typeof p==='function' ? db.get(t).find(p).value() : db.get(t).find(p).value(),
all: (t,p) => typeof p==='function' ? db.get(t).filter(p).value() : (p ? db.get(t).filter(p).value() : db.get(t).value()),
add: (t,r) => { db.get(t).push(Object.assign({},r,{created_at:r.created_at||new Date().toISOString()})).write(); return r; },
set: (t,p,c) => { db.get(t).find(p).assign(Object.assign({},c,{updated_at:new Date().toISOString()})).write(); },
del: (t,p) => { db.get(t).remove(p).write(); },
cnt: (t,p) => p ? db.get(t).filter(p).value().length : db.get(t).value().length,
inc: (t,p,f,n) => { n=n||1; const r=db.get(t).find(p).value(); if(r) db.get(t).find(p).assign({[f]:(r[f]||0)+n}).write(); },
userFull: (email) => {
const u=db.get('users').find(function(x){return x.email.toLowerCase()===email.toLowerCase();}).value();
if(!u) return null;
const o=db.get('organisations').find({id:u.org_id}).value();
return Object.assign({},u,{org_name:o&&o.name,plan:o&&o.plan,org_status:o&&o.status,questionnaire_limit:o&&o.questionnaire_limit,questionnaires_used:o&&o.questionnaires_used,logo_url:o&&o.logo_url,branding_name:o&&o.branding_name});
},
userById: (id) => {
const u=db.get('users').find({id:id}).value();
if(!u) return null;
const o=db.get('organisations').find({id:u.org_id}).value();
return Object.assign({},u,{org_name:o&&o.name,plan:o&&o.plan,org_status:o&&o.status,questionnaire_limit:o&&o.questionnaire_limit,questionnaires_used:o&&o.questionnaires_used,logo_url:o&&o.logo_url,branding_name:o&&o.branding_name});
}
};

const mailer = nodemailer.createTransport({host:process.env.SMTP_HOST||'smtp.gmail.com',port:parseInt(process.env.SMTP_PORT||'587'),secure:false,auth:{user:process.env.SMTP_USER,pass:process.env.SMTP_PASS},tls:{rejectUnauthorized:false}});
const FROM = process.env.SMTP_FROM||'CybeSure SecureAnswer <noreply@cybersure.co.uk>';
const wrap = function(b,org) {
org = org||'';
return '<!DOCTYPE html><html><head><meta charset="UTF-8"><style>body{font-family:Calibri,Arial,sans-serif;background:#f0f4ff}.w{max-width:560px;margin:30px auto;background:#fff;border-radius:12px;overflow:hidden}.h{background:#0d1b3e;padding:20px 26px}.logo{font-size:20px;font-weight:800;color:#fff}.b{padding:26px}.t{font-size:18px;font-weight:700;color:#0d1b3e;margin-bottom:10px}p{font-size:14px;color:#444;line-height:1.7;margin-bottom:10px}.btn{display:inline-block;padding:12px 26px;background:#4a9edd;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:13px}.box{background:#eef4ff;border-left:4px solid #4a9edd;padding:13px 17px;margin:14px 0;font-size:13px}.code{font-family:monospace;font-size:17px;font-weight:700;color:#0d1b3e}.warn{background:#fff8e6;border-left:4px solid #f5a623;padding:11px 15px;margin:10px 0;font-size:13px;color:#555}.f{padding:14px;background:#f8faff;border-top:1px solid #e0e8f0;font-size:11px;color:#999;text-align:center}</style></head><body><div class="w"><div class="h"><div class="logo">CybeSure</div>'+(org?'<div style="font-size:11px;color:#7a90b8;margin-top:3px">On behalf of '+org+'</div>':'')+'</div><div class="b">'+b+'</div><div class="f">2025 CybeSure Ltd. cybersure.co.uk. support@cybersure.co.uk. Powered by CybeSure SecureAnswer</div></div></body></html>';
};
const sendEmail = async function(to,subject,html) {
if(!process.env.SMTP_USER){console.log('EMAIL SKIPPED: '+subject);return;}
try{await mailer.sendMail({from:FROM,to:to,subject:subject,html:html});console.log('Email sent: '+subject);}
catch(e){console.error('Email failed: '+e.message);}
};

app.use(helmet({contentSecurityPolicy:false}));
app.use(cors({origin:(process.env.ALLOWED_ORIGINS||'*').split(','),credentials:true,methods:['GET','POST','PUT','PATCH','DELETE','OPTIONS'],allowedHeaders:['Content-Type','Authorization','x-admin-key']}));
app.use(rateLimit({windowMs:15*60*1000,max:500}));
app.use(express.json({limit:'10mb'}));
app.use(express.urlencoded({extended:true}));
app.use(express.static(path.join(__dirname,'public')));

const auth = function(req,res,next) {
try {
const h=req.headers.authorization;
if(!h||!h.startsWith('Bearer ')) return res.status(401).json({error:'No token'});
const d=jwt.verify(h.split(' ')[1],process.env.JWT_SECRET||'dev-secret-change-this');
const u=Q.userById(d.userId);
if(!u) return res.status(401).json({error:'User not found'});
if(u.status!=='active') return res.status(403).json({error:'Account suspended'});
if(u.org_status&&u.org_status!=='active') return res.status(403).json({error:'Organisation suspended'});
req.user=u; next();
} catch(e) {
if(e.name==='TokenExpiredError') return res.status(401).json({error:'Session expired'});
return res.status(401).json({error:'Invalid token'});
}
};

const role = function() {
const roles = Array.prototype.slice.call(arguments);
return function(req,res,next) {
if(!roles.includes(req.user&&req.user.role)) return res.status(403).json({error:'Insufficient permissions'});
next();
};
};

const limitCheck = function(req,res,next) {
const o=Q.one('organisations',{id:req.user.org_id});
if(!o) return res.status(404).json({error:'Organisation not found'});
if(o.questionnaire_limit!==999999&&o.questionnaires_used>=o.questionnaire_limit) return res.status(402).json({error:'Questionnaire limit reached',code:'LIMIT_REACHED',used:o.questionnaires_used,limit:o.questionnaire_limit,plan:o.plan});
req.orgLimits=o; next();
};

app.get('/api/health',function(req,res){res.json({status:'ok',version:'1.0.0',db:'lowdb',ts:new Date().toISOString()});});

app.post('/api/auth/login',rateLimit({windowMs:15*60*1000,max:10}),async function(req,res){
try{
const email=req.body.email;const password=req.body.password;
if(!email||!password) return res.status(400).json({error:'Email and password required'});
const u=Q.userFull(email.trim());
if(!u) return res.status(401).json({error:'Invalid email or password'});
if(u.status!=='active') return res.status(403).json({error:'Account suspended'});
const valid=await bcrypt.compare(password,u.password_hash);
if(!valid) return res.status(401).json({error:'Invalid email or password'});
Q.set('users',{id:u.id},{last_login:new Date().toISOString()});
const token=jwt.sign({userId:u.id,orgId:u.org_id,role:u.role},process.env.JWT_SECRET||'dev-secret-change-this',{expiresIn:'8h'});
res.json({token:token,mustChangePw:!!u.must_change_pw,user:{id:u.id,email:u.email,firstName:u.first_name,lastName:u.last_name,role:u.role,orgId:u.org_id,orgName:u.org_name,plan:u.plan,logoUrl:u.logo_url,brandingName:u.branding_name,limits:{questionnaires:u.questionnaire_limit,used:u.questionnaires_used}}});
}catch(e){console.error(e);res.status(500).json({error:'Login failed'});}
});

app.get('/api/auth/me',auth,function(req,res){
const u=req.user;
res.json({id:u.id,email:u.email,firstName:u.first_name,lastName:u.last_name,role:u.role,orgId:u.org_id,orgName:u.org_name,plan:u.plan,mustChangePw:!!u.must_change_pw,logoUrl:u.logo_url,brandingName:u.branding_name,limits:{questionnaires:u.questionnaire_limit,used:u.questionnaires_used}});
});

app.post('/api/auth/change-password',auth,async function(req,res){
try{
const currentPassword=req.body.currentPassword;const newPassword=req.body.newPassword;
if(!newPassword||newPassword.length<8) return res.status(400).json({error:'Password must be at least 8 characters'});
if(!/(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/.test(newPassword)) return res.status(400).json({error:'Password must contain uppercase, lowercase and a number'});
const u=Q.one('users',{id:req.user.id});
if(!req.user.must_change_pw){
const ok=await bcrypt.compare(currentPassword,u.password_hash);
if(!ok) return res.status(400).json({error:'Current password incorrect'});
}
const hash=await bcrypt.hash(newPassword,12);
Q.set('users',{id:req.user.id},{password_hash:hash,must_change_pw:false});
res.json({message:'Password changed successfully'});
}catch(e){res.status(500).json({error:'Failed to change password'});}
});

app.post('/api/auth/forgot-password',async function(req,res){
res.json({message:'If this email exists a reset link has been sent.'});
try{
const u=Q.one('users',function(x){return x.email.toLowerCase()===(req.body.email||'').toLowerCase()&&x.status==='active';});
if(u){
const t=crypto.randomBytes(32).toString('hex');
Q.set('users',{id:u.id},{reset_token:t,reset_expires:new Date(Date.now()+3600000).toISOString()});
const url=(process.env.APP_URL||'')+'/reset-password?token='+t;
await sendEmail(u.email,'Password Reset',wrap('<div class="t">Reset Password</div><p>Click below to reset. Link expires in 1 hour.</p><a href="'+url+'" class="btn">Reset Password</a>'));
}
}catch(e){console.error(e);}
});

app.get('/api/admin/stats',auth,role('superadmin'),function(req,res){
const orgs=Q.all('organisations',function(o){return o.id!=='cs-admin-org';});
const runs=Q.all('questionnaire_runs');
const avgC=runs.length?Math.round(runs.reduce(function(s,r){return s+(r.avg_confidence||0);},0)/runs.length):0;
res.json({organisations:{total:orgs.length,active:orgs.filter(function(o){return o.status==='active';}).length},users:{total:Q.cnt('users',function(u){return u.role!=='superadmin';})},questionnaires:{total:runs.length,avgConfidence:avgC},suppliers:{total:Q.cnt('suppliers')}});
});

app.get('/api/admin/organisations',auth,role('superadmin'),function(req,res){
const orgs=Q.all('organisations',function(o){return o.id!=='cs-admin-org';}).map(function(o){
return Object.assign({},o,{user_count:Q.cnt('users',function(u){return u.org_id===o.id&&u.role!=='superadmin';}),run_count:Q.cnt('questionnaire_runs',{org_id:o.id}),supplier_count:Q.cnt('suppliers',{org_id:o.id})});
});
res.json(orgs.sort(function(a,b){return new Date(b.created_at)-new Date(a.created_at);}));
});

app.post('/api/admin/organisations',auth,role('superadmin'),async function(req,res){
try{
const orgName=req.body.orgName;const domain=req.body.domain;const plan=req.body.plan;
const adminEmail=req.body.adminEmail;const adminFirstName=req.body.adminFirstName;
const adminLastName=req.body.adminLastName;const logoUrl=req.body.logoUrl;const brandingName=req.body.brandingName;
if(!orgName||!adminEmail) return res.status(400).json({error:'Organisation name and admin email required'});
if(Q.one('users',function(u){return u.email.toLowerCase()===adminEmail.toLowerCase();})) return res.status(400).json({error:'Email already registered'});
const limits={starter:25,professional:100,enterprise:300,unlimited:999999};
const orgId=uuid();
Q.add('organisations',{id:orgId,name:orgName,domain:domain||'',plan:plan||'starter',questionnaire_limit:limits[plan]||25,questionnaires_used:0,supplier_limit:10,suppliers_used:0,logo_url:logoUrl||'',branding_name:brandingName||orgName,status:'active',woo_customer_id:''});
const tempPw=crypto.randomBytes(8).toString('hex');
const hash=await bcrypt.hash(tempPw,12);
Q.add('users',{id:uuid(),org_id:orgId,email:adminEmail,password_hash:hash,first_name:adminFirstName||'',last_name:adminLastName||'',role:'org_admin',status:'active',must_change_pw:true});
await sendEmail(adminEmail,'Welcome to CybeSure SecureAnswer',wrap('<div class="t">Welcome</div><p>Hi '+(adminFirstName||'there')+', your <strong>'+orgName+'</strong> account is active.</p><div class="box"><p><strong>URL:</strong> '+process.env.APP_URL+'</p><p><strong>Email:</strong> '+adminEmail+'</p><p><strong>Password:</strong> <span class="code">'+tempPw+'</span></p></div><div class="warn">Change your password on first login.</div><a href="'+process.env.APP_URL+'" class="btn">Log In</a>'));
res.status(201).json({orgId:orgId,tempPassword:tempPw,message:'Organisation created'});
}catch(e){console.error(e);res.status(500).json({error:'Failed to create organisation'});}
});

app.patch('/api/admin/organisations/:id',auth,role('superadmin'),function(req,res){
const plan=req.body.plan;const status=req.body.status;const questionnaireLimit=req.body.questionnaireLimit;
const logoUrl=req.body.logoUrl;const brandingName=req.body.brandingName;
const limits={starter:25,professional:100,enterprise:300,unlimited:999999};
const c={};
if(plan){c.plan=plan;c.questionnaire_limit=limits[plan]||25;}
if(status)c.status=status;
if(questionnaireLimit)c.questionnaire_limit=questionnaireLimit;
if(logoUrl!==undefined)c.logo_url=logoUrl;
if(brandingName!==undefined)c.branding_name=brandingName;
Q.set('organisations',{id:req.params.id},c);
res.json({message:'Updated'});
});

app.post('/api/admin/organisations/:id/topup',auth,role('superadmin'),function(req,res){
const add=req.body.add||10;
Q.inc('organisations',{id:req.params.id},'questionnaire_limit',add);
const o=Q.one('organisations',{id:req.params.id});
res.json({message:'Added '+add+' questionnaires',questionnaire_limit:o.questionnaire_limit});
});

app.post('/api/admin/organisations/:id/reset-usage',auth,role('superadmin'),function(req,res){
Q.set('organisations',{id:req.params.id},{questionnaires_used:0,suppliers_used:0});
res.json({message:'Usage reset'});
});

app.get('/api/users',auth,role('superadmin','org_admin'),function(req,res){
const orgId=req.user.role==='superadmin'?(req.query.orgId||req.user.org_id):req.user.org_id;
res.json(Q.all('users',function(u){return u.org_id===orgId;}).map(function(u){return {id:u.id,email:u.email,first_name:u.first_name,last_name:u.last_name,role:u.role,status:u.status,last_login:u.last_login,created_at:u.created_at};}).sort(function(a,b){return new Date(b.created_at)-new Date(a.created_at);}));
});

app.post('/api/users/invite',auth,role('superadmin','org_admin'),async function(req,res){
try{
const inviteEmail=req.body.inviteEmail;const firstName=req.body.firstName;
const lastName=req.body.lastName;const r=req.body.role;
if(!inviteEmail) return res.status(400).json({error:'Email required'});
if(Q.one('users',function(u){return u.email.toLowerCase()===inviteEmail.toLowerCase();})) return res.status(400).json({error:'Email already registered'});
const tempPw=crypto.randomBytes(8).toString('hex');
const hash=await bcrypt.hash(tempPw,12);
Q.add('users',{id:uuid(),org_id:req.user.org_id,email:inviteEmail,password_hash:hash,first_name:firstName||'',last_name:lastName||'',role:r||'user',status:'active',must_change_pw:true});
const o=Q.one('organisations',{id:req.user.org_id});
await sendEmail(inviteEmail,'Invited to CybeSure SecureAnswer',wrap('<div class="t">You have been invited</div><p>Added to <strong>'+(o&&o.name)+'</strong>.</p><div class="box"><p><strong>URL:</strong> '+process.env.APP_URL+'</p><p><strong>Email:</strong> '+inviteEmail+'</p><p><strong>Password:</strong> <span class="code">'+tempPw+'</span></p></div><a href="'+process.env.APP_URL+'" class="btn">Log In</a>'));
res.status(201).json({message:'User invited',tempPassword:tempPw});
}catch(e){console.error(e);res.status(500).json({error:'Failed to invite user'});}
});

app.patch('/api/users/:id',auth,role('superadmin','org_admin'),function(req,res){
const c={};
if(req.body.role)c.role=req.body.role;
if(req.body.status)c.status=req.body.status;
Q.set('users',{id:req.params.id},c);
res.json({message:'Updated'});
});

app.get('/api/knowledge',auth,function(req,res){
res.json(Q.all('knowledge_sources',{org_id:req.user.org_id}).sort(function(a,b){return new Date(b.created_at)-new Date(a.created_at);}));
});

app.post('/api/knowledge',auth,role('superadmin','org_admin','manager'),function(req,res){
const name=req.body.name;const type=req.body.type;
if(!name||!type) return res.status(400).json({error:'Name and type required'});
const s={id:uuid(),org_id:req.user.org_id,name:name,type:type,site_url:req.body.siteUrl||'',library_name:req.body.libraryName||'',tenant_id:req.body.tenantId||'',client_id:req.body.clientId||'',client_secret:req.body.clientSecret?Buffer.from(req.body.clientSecret).toString('base64'):'',doc_count:Math.floor(Math.random()*15)+5,last_scanned:new Date().toISOString(),status:'connected'};
Q.add('knowledge_sources',s);
res.status(201).json(s);
});

app.delete('/api/knowledge/:id',auth,role('superadmin','org_admin','manager'),function(req,res){
Q.del('knowledge_sources',{id:req.params.id,org_id:req.user.org_id});
res.json({message:'Removed'});
});

app.get('/api/questionnaires',auth,function(req,res){
const orgId=req.user.role==='superadmin'?(req.query.orgId||req.user.org_id):req.user.org_id;
res.json(Q.all('questionnaire_runs',{org_id:orgId}).sort(function(a,b){return new Date(b.created_at)-new Date(a.created_at);}).slice(0,50));
});

app.post('/api/questionnaires/run',auth,role('superadmin','org_admin','manager'),limitCheck,function(req,res){
const fileName=req.body.fileName;const title=req.body.title;
if(!fileName) return res.status(400).json({error:'File name required'});
const runId=uuid();
Q.add('questionnaire_runs',{id:runId,org_id:req.user.org_id,user_id:req.user.id,file_name:fileName,title:title||fileName,type:'internal',status:'processing',total_questions:0,answered:0,avg_confidence:0});
Q.inc('organisations',{id:req.user.org_id},'questionnaires_used',1);
res.status(201).json({runId:runId,sources:Q.all('knowledge_sources',{org_id:req.user.org_id,status:'connected'})});
});

app.post('/api/questionnaires/runs/:runId/questions',auth,function(req,res){
if(!Q.one('questionnaire_runs',{id:req.params.runId,org_id:req.user.org_id})) return res.status(404).json({error:'Run not found'});
const id=uuid();
Q.add('questions',{id:id,run_id:req.params.runId,org_id:req.user.org_id,question_num:req.body.questionNum,question_text:req.body.questionText,category:req.body.category,question_type:req.body.questionType,answer:req.body.answer,confidence:req.body.confidence,reasoning:req.body.reasoning,sources:JSON.stringify(req.body.sources||[]),improvements:JSON.stringify(req.body.improvements||[]),review_status:'pending'});
res.status(201).json({id:id});
});

app.patch('/api/questionnaires/runs/:runId/complete',auth,function(req,res){
const qs=Q.all('questions',{run_id:req.params.runId});
const n=qs.length;
const avg=n?Math.round(qs.reduce(function(s,q){return s+(q.confidence||0);},0)/n):0;
Q.set('questionnaire_runs',{id:req.params.runId,org_id:req.user.org_id},{status:'completed',total_questions:n,answered:n,avg_confidence:avg,high_confidence:qs.filter(function(q){return q.confidence>=75;}).length,med_confidence:qs.filter(function(q){return q.confidence>=50&&q.confidence<75;}).length,low_confidence:qs.filter(function(q){return q.confidence<50;}).length,completed_at:new Date().toISOString()});
res.json({message:'Completed'});
});

app.get('/api/questionnaires/runs/:runId',auth,function(req,res){
const run=Q.one('questionnaire_runs',{id:req.params.runId,org_id:req.user.org_id});
if(!run) return res.status(404).json({error:'Run not found'});
res.json({run:run,questions:Q.all('questions',{run_id:req.params.runId}).map(function(q){return Object.assign({},q,{sources:JSON.parse(q.sources||'[]'),improvements:JSON.parse(q.improvements||'[]')});})});
});

app.get('/api/suppliers',auth,function(req,res){
res.json(Q.all('suppliers',{org_id:req.user.org_id}).sort(function(a,b){return a.name.localeCompare(b.name);}));
});

app.post('/api/suppliers',auth,role('superadmin','org_admin','manager'),function(req,res){
if(!req.body.name) return res.status(400).json({error:'Supplier name required'});
const s={id:uuid(),org_id:req.user.org_id,name:req.body.name,domain:req.body.domain||'',contact_name:req.body.contactName||'',contact_email:req.body.contactEmail||'',category:req.body.category||'',criticality:req.body.criticality||'medium',overall_score:0,risk_level:'unknown',notes:req.body.notes||'',status:'active',updated_at:new Date().toISOString()};
Q.add('suppliers',s);
res.status(201).json(s);
});

app.patch('/api/suppliers/:id',auth,role('superadmin','org_admin','manager'),function(req,res){
const c={};
if(req.body.name)c.name=req.body.name;if(req.body.domain)c.domain=req.body.domain;
if(req.body.contactName)c.contact_name=req.body.contactName;if(req.body.contactEmail)c.contact_email=req.body.contactEmail;
if(req.body.category)c.category=req.body.category;if(req.body.criticality)c.criticality=req.body.criticality;
if(req.body.notes)c.notes=req.body.notes;if(req.body.status)c.status=req.body.status;
Q.set('suppliers',{id:req.params.id,org_id:req.user.org_id},c);
res.json({message:'Updated'});
});

app.delete('/api/suppliers/:id',auth,role('superadmin','org_admin','manager'),function(req,res){
Q.del('suppliers',{id:req.params.id,org_id:req.user.org_id});
res.json({message:'Deleted'});
});

app.get('/api/suppliers/dashboard',auth,function(req,res){
const s=Q.all('suppliers',{org_id:req.user.org_id,status:'active'});
const avg=s.length?Math.round(s.reduce(function(x,y){return x+(y.overall_score||0);},0)/s.length):0;
res.json({summary:{total:s.length,avg_score:avg,critical:s.filter(function(x){return x.risk_level==='critical';}).length,high:s.filter(function(x){return x.risk_level==='high';}).length,medium:s.filter(function(x){return x.risk_level==='medium';}).length,low:s.filter(function(x){return x.risk_level==='low';}).length,unknown:s.filter(function(x){return x.risk_level==='unknown';}).length},recentAssessments:Q.all('supplier_assessments',{org_id:req.user.org_id,status:'completed'}).sort(function(a,b){return new Date(b.completed_at)-new Date(a.completed_at);}).slice(0,8)});
});

app.post('/api/assessments/send',auth,role('superadmin','org_admin','manager'),async function(req,res){
try{
if(!req.body.supplierEmail) return res.status(400).json({error:'Supplier email required'});
const token=crypto.randomBytes(32).toString('hex');
const expires=new Date(Date.now()+30*24*60*60*1000).toISOString();
const id=uuid();
Q.add('supplier_assessments',{id:id,org_id:req.user.org_id,supplier_id:req.body.supplierId||null,sent_by:req.user.id,secure_token:token,token_expires:expires,status:'sent',supplier_email:req.body.supplierEmail,supplier_name:req.body.supplierName||''});
const org=Q.one('organisations',{id:req.user.org_id});
const url=(process.env.APP_URL||'https://app.cybersure.co.uk')+'/assess/'+token;
await sendEmail(req.body.supplierEmail,'Security Assessment from '+(org&&org.name),wrap('<div class="t">Assessment Request</div><p>Dear '+(req.body.supplierName||'Supplier')+',</p><p><strong>'+(org&&org.name)+'</strong> has sent you a security questionnaire.</p><div class="box"><strong>'+(req.body.title||'Security Assessment')+'</strong></div><a href="'+url+'" class="btn">Complete Assessment</a><div class="warn">This link is unique to you.</div>',(org&&org.name)));
res.status(201).json({assessmentId:id,assessmentUrl:url});
}catch(e){console.error(e);res.status(500).json({error:'Failed to send'});}
});

app.get('/api/assessments/token/:token',function(req,res){
const a=Q.one('supplier_assessments',{secure_token:req.params.token});
if(!a||new Date(a.token_expires)<new Date()) return res.status(404).json({error:'Assessment link is invalid or has expired.'});
const org=Q.one('organisations',{id:a.org_id});
if(a.status==='sent') Q.set('supplier_assessments',{id:a.id},{status:'in_progress'});
res.json({assessment:Object.assign({},a,{org_name:org&&org.name,logo_url:org&&org.logo_url,branding_name:org&&org.branding_name}),completed:a.status==='completed'});
});

app.post('/api/assessments/token/:token/submit',async function(req,res){
try{
const answers=req.body.answers;
if(!answers||!answers.length) return res.status(400).json({error:'Answers required'});
const a=Q.one('supplier_assessments',function(s){return s.secure_token===req.params.token&&s.status!=='completed';});
if(!a||new Date(a.token_expires)<new Date()) return res.status(404).json({error:'Assessment not found or expired'});
const avg=Math.round(answers.reduce(function(s,q){return s+(q.confidence||0);},0)/answers.length);
const risk=avg>=75?'low':avg>=50?'medium':avg>=25?'high':'critical';
Q.set('supplier_assessments',{id:a.id},{status:'completed',score:avg,risk_level:risk,completed_at:new Date().toISOString()});
if(a.supplier_id) Q.set('suppliers',{id:a.supplier_id},{overall_score:avg,risk_level:risk,last_assessed:new Date().toISOString()});
Q.all('users',function(u){return u.org_id===a.org_id&&(u.role==='org_admin'||u.role==='manager');}).forEach(function(u){sendEmail(u.email,'Assessment Complete - '+a.supplier_name,wrap('<div class="t">Assessment Completed</div><p><strong>'+a.supplier_name+'</strong> submitted their assessment.</p><div class="box"><p><strong>Score:</strong> '+avg+'%</p><p><strong>Risk:</strong> '+risk+'</p></div><a href="'+process.env.APP_URL+'/suppliers" class="btn">View Results</a>'));});
res.json({message:'Submitted',score:avg,riskLevel:risk});
}catch(e){console.error(e);res.status(500).json({error:'Failed to submit'});}
});

app.get('/api/assessments',auth,function(req,res){
res.json(Q.all('supplier_assessments',{org_id:req.user.org_id}).sort(function(a,b){return new Date(b.created_at)-new Date(a.created_at);}));
});

app.get('/api/dashboard',auth,function(req,res){
const org=Q.one('organisations',{id:req.user.org_id});
const runs=Q.all('questionnaire_runs',{org_id:req.user.org_id});
const supps=Q.all('suppliers',{org_id:req.user.org_id,status:'active'});
const ago30=new Date(Date.now()-30*24*60*60*1000);
const avg=runs.length?Math.round(runs.reduce(function(s,r){return s+(r.avg_confidence||0);},0)/runs.length):0;
res.json({organisation:{name:org.name,plan:org.plan,status:org.status,logoUrl:org.logo_url,brandingName:org.branding_name},usage:{questionnaires:{used:org.questionnaires_used,limit:org.questionnaire_limit,pct:Math.round((org.questionnaires_used/Math.max(org.questionnaire_limit,1))*100)}},stats:{totalRuns:runs.length,avgConfidence:avg,thisMonth:runs.filter(function(r){return new Date(r.created_at)>ago30;}).length,totalSuppliers:supps.length,criticalSuppliers:supps.filter(function(s){return s.risk_level==='critical';}).length,highRiskSuppliers:supps.filter(function(s){return s.risk_level==='high';}).length,unassessedSuppliers:supps.filter(function(s){return s.risk_level==='unknown';}).length,knowledgeSources:Q.cnt('knowledge_sources',{org_id:req.user.org_id,status:'connected'})},pendingAssessments:Q.all('supplier_assessments',function(a){return a.org_id===req.user.org_id&&(a.status==='sent'||a.status==='in_progress');}).slice(0,5)});
});

app.post('/webhook/woocommerce',express.raw({type:'*/*'}),async function(req,res){
try{
const event=req.headers['x-wc-webhook-topic'];
const data=JSON.parse(req.body.toString());
const limits={starter:25,professional:100,enterprise:300,unlimited:999999};
const getPlan=function(d){return (d.meta_data||[]).find(function(m){return m.key==='questionnaire_plan';})||{value:'starter'};};
const cid=String(data.customer_id);
if(event==='subscription.created'||event==='order.created'){
const em=data.billing&&data.billing.email;
if(!em) return res.status(200).json({received:true});
if(!Q.one('organisations',{woo_customer_id:cid})){
const plan=getPlan(data).value;const oid=uuid();
Q.add('organisations',{id:oid,name:(data.billing&&data.billing.company)||em,plan:plan,questionnaire_limit:limits[plan]||25,questionnaires_used:0,supplier_limit:10,suppliers_used:0,woo_customer_id:cid,status:'active',logo_url:'',branding_name:''});
const pw=crypto.randomBytes(8).toString('hex');
const hash=await bcrypt.hash(pw,12);
Q.add('users',{id:uuid(),org_id:oid,email:em,password_hash:hash,first_name:(data.billing&&data.billing.first_name)||'',last_name:(data.billing&&data.billing.last_name)||'',role:'org_admin',status:'active',must_change_pw:true});
await sendEmail(em,'Welcome to CybeSure SecureAnswer',wrap('<div class="t">Your Account is Ready</div><p>Thank you for subscribing.</p><div class="box"><p><strong>URL:</strong> '+process.env.APP_URL+'</p><p><strong>Email:</strong> '+em+'</p><p><strong>Password:</strong> <span class="code">'+pw+'</span></p></div><a href="'+process.env.APP_URL+'" class="btn">Log In Now</a>'));
}
}
if(event==='subscription.updated'){const plan=getPlan(data).value;Q.set('organisations',{woo_customer_id:cid},{plan:plan,questionnaire_limit:limits[plan]||25});}
if(event==='subscription.renewed') Q.set('organisations',{woo_customer_id:cid},{questionnaires_used:0,suppliers_used:0});
if(event==='subscription.cancelled'||event==='subscription.on-hold') Q.set('organisations',{woo_customer_id:cid},{status:event==='subscription.cancelled'?'cancelled':'suspended'});
res.status(200).json({received:true});
}catch(e){console.error('Webhook:',e);res.status(200).json({received:true});}
});

app.get('*',function(req,res){
if(!req.path.startsWith('/api')&&!req.path.startsWith('/webhook')) res.sendFile(path.join(__dirname,'public','index.html'));
});

app.listen(PORT,function(){console.log('CybeSure SecureAnswer running on port '+PORT);});
