"""
CybeSure SecureAnswer — Auth & Subscription Routes
© CybeSure Ltd. All rights reserved.
"""
import os
import stripe
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from typing import Optional, List

from database import (
    get_db, User, Organisation, QuestionnaireRun, TopupPurchase,
    UserRole, SubscriptionTier, SubscriptionStatus,
    SUBSCRIPTION_CONFIG, TOPUP_CONFIG
)
from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_role, get_org, check_subscription
)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RegisterOrgRequest(BaseModel):
    org_name: str
    contact_email: str
    admin_name: str
    admin_email: str
    password: str
    tier: SubscriptionTier = SubscriptionTier.STARTER

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    user_name: str
    user_role: str
    org_id: int
    org_name: str
    subscription_tier: str
    questionnaires_remaining: int
    subscription_expires: str

class InviteUserRequest(BaseModel):
    email: str
    full_name: str
    role: UserRole
    password: str

class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: str

class OrgDashboardResponse(BaseModel):
    org_name: str
    tier: str
    status: str
    questionnaire_limit: int
    questionnaires_used: int
    topup_credits: int
    remaining: int
    subscription_start: str
    subscription_end: str
    users_count: int
    recent_runs: list

class TopupRequest(BaseModel):
    topup_type: str  # "single" or "bundle"


# ── Auth routes ───────────────────────────────────────────────────────────────

@router.post("/auth/register")
async def register_organisation(req: RegisterOrgRequest, db: Session = Depends(get_db)):
    """Register a new organisation with an admin user."""
    # Check email not already used
    if db.query(User).filter(User.email == req.admin_email).first():
        raise HTTPException(400, "Email already registered")

    # Create organisation
    slug = req.org_name.lower().replace(" ", "-").replace("'", "")[:50]
    # Ensure unique slug
    base_slug = slug
    counter = 1
    while db.query(Organisation).filter(Organisation.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    config = SUBSCRIPTION_CONFIG[req.tier]
    org = Organisation(
        name=req.org_name,
        slug=slug,
        contact_email=req.contact_email,
        tier=req.tier,
        status=SubscriptionStatus.TRIAL,
        questionnaire_limit=config["limit"],
        subscription_start=datetime.utcnow(),
        subscription_end=datetime.utcnow() + timedelta(days=14)  # 14-day trial
    )
    db.add(org)
    db.flush()

    # Create admin user
    admin = User(
        org_id=org.id,
        email=req.admin_email,
        full_name=req.admin_name,
        hashed_password=hash_password(req.password),
        role=UserRole.ADMIN,
        is_active=True
    )
    db.add(admin)
    db.commit()

    return {
        "message": "Organisation registered successfully. 14-day trial activated.",
        "org_id": org.id,
        "org_slug": org.slug,
        "trial_expires": org.subscription_end.isoformat()
    }


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(401, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is inactive. Contact your administrator.")

    org = db.query(Organisation).filter(Organisation.id == user.org_id).first()

    # Update last login
    user.last_login = datetime.utcnow()
    db.commit()

    token = create_token({"sub": str(user.id), "org_id": user.org_id, "role": user.role})

    return LoginResponse(
        access_token=token,
        user_id=user.id,
        user_name=user.full_name,
        user_role=user.role.value,
        org_id=org.id,
        org_name=org.name,
        subscription_tier=org.tier.value,
        questionnaires_remaining=org.remaining,
        subscription_expires=org.subscription_end.isoformat()
    )


@router.get("/auth/me")
async def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.query(Organisation).filter(Organisation.id == current_user.org_id).first()
    return {
        "user_id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role.value,
        "org_id": org.id,
        "org_name": org.name,
        "tier": org.tier.value,
        "status": org.status.value,
        "remaining": org.remaining,
        "total_limit": org.total_limit,
        "used": org.questionnaires_used,
        "subscription_end": org.subscription_end.isoformat(),
        "is_active": org.is_active
    }


# ── User management (Admin only) ──────────────────────────────────────────────

@router.post("/admin/users/invite")
async def invite_user(
    req: InviteUserRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN))
):
    """Admin can add users to their organisation."""
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(400, "Email already registered")

    user = User(
        org_id=admin.org_id,
        email=req.email,
        full_name=req.full_name,
        hashed_password=hash_password(req.password),
        role=req.role,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": f"User {req.full_name} added as {req.role.value}",
        "user_id": user.id
    }


@router.get("/admin/users")
async def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN))
):
    users = db.query(User).filter(User.org_id == admin.org_id).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role.value,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
            "last_login": u.last_login.isoformat() if u.last_login else None
        }
        for u in users
    ]


@router.patch("/admin/users/{user_id}")
async def update_user(
    user_id: int,
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN))
):
    user = db.query(User).filter(
        User.id == user_id, User.org_id == admin.org_id
    ).first()
    if not user:
        raise HTTPException(404, "User not found")
    if role: user.role = role
    if is_active is not None: user.is_active = is_active
    db.commit()
    return {"message": "User updated"}


@router.delete("/admin/users/{user_id}")
async def remove_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(UserRole.ADMIN))
):
    user = db.query(User).filter(
        User.id == user_id, User.org_id == admin.org_id
    ).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot remove yourself")
    user.is_active = False
    db.commit()
    return {"message": "User deactivated"}


# ── Organisation dashboard ────────────────────────────────────────────────────

@router.get("/org/dashboard")
async def org_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    org = db.query(Organisation).filter(Organisation.id == current_user.org_id).first()
    users_count = db.query(User).filter(User.org_id == org.id, User.is_active == True).count()
    recent_runs = db.query(QuestionnaireRun).filter(
        QuestionnaireRun.org_id == org.id
    ).order_by(QuestionnaireRun.created_at.desc()).limit(10).all()

    config = SUBSCRIPTION_CONFIG[org.tier]
    days_remaining = max(0, (org.subscription_end - datetime.utcnow()).days)

    return {
        "org_name": org.name,
        "tier": org.tier.value,
        "tier_name": config["name"],
        "status": org.status.value,
        "is_active": org.is_active,
        "questionnaire_limit": org.questionnaire_limit,
        "questionnaires_used": org.questionnaires_used,
        "topup_credits": org.topup_credits,
        "remaining": org.remaining,
        "total_limit": org.total_limit,
        "subscription_start": org.subscription_start.isoformat(),
        "subscription_end": org.subscription_end.isoformat(),
        "days_remaining": days_remaining,
        "users_count": users_count,
        "recent_runs": [
            {
                "id": r.id,
                "filename": r.filename,
                "question_count": r.question_count,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
                "is_topup": r.is_topup
            }
            for r in recent_runs
        ],
        "subscription_plans": {
            tier.value: {
                "name": cfg["name"],
                "limit": cfg["limit"],
                "price_gbp": cfg["price_gbp"],
                "description": cfg["description"]
            }
            for tier, cfg in SUBSCRIPTION_CONFIG.items()
        },
        "topup_options": TOPUP_CONFIG
    }


# ── Stripe payment integration ────────────────────────────────────────────────

@router.post("/billing/create-topup-session")
async def create_topup_session(
    req: TopupRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Create a Stripe Checkout session for top-up purchase."""
    if not stripe.api_key:
        raise HTTPException(503, "Payment processing not configured. Contact support@cybesure.com")

    topup = TOPUP_CONFIG.get(req.topup_type)
    if not topup:
        raise HTTPException(400, "Invalid top-up type. Use 'single' or 'bundle'")

    org = db.query(Organisation).filter(Organisation.id == current_user.org_id).first()
    app_url = os.environ.get("APP_URL", "https://cybesure-qa-platform.onrender.com")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {
                        "name": f"CybeSure SecureAnswer — {topup['label']}",
                        "description": f"Additional questionnaire credits for {org.name}"
                    },
                    "unit_amount": int(topup["price_gbp"] * 100),  # pence
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{app_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_url}/billing/cancel",
            customer_email=current_user.email,
            metadata={
                "org_id": str(org.id),
                "topup_type": req.topup_type,
                "qty": str(topup["qty"]),
                "user_id": str(current_user.id)
            }
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))


@router.post("/billing/create-subscription-session")
async def create_subscription_session(
    tier: SubscriptionTier,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db)
):
    """Create a Stripe Checkout session for annual subscription."""
    if not stripe.api_key:
        raise HTTPException(503, "Payment processing not configured. Contact support@cybesure.com")

    config = SUBSCRIPTION_CONFIG[tier]
    org = db.query(Organisation).filter(Organisation.id == current_user.org_id).first()
    app_url = os.environ.get("APP_URL", "https://cybesure-qa-platform.onrender.com")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {
                        "name": f"CybeSure SecureAnswer — {config['name']} Plan",
                        "description": config["description"]
                    },
                    "unit_amount": int(config["price_gbp"] * 100),
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"{app_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_url}/billing/cancel",
            customer_email=current_user.email,
            metadata={
                "org_id": str(org.id),
                "tier": tier.value,
                "limit": str(config["limit"]),
                "type": "subscription",
                "user_id": str(current_user.id)
            }
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))


@router.post("/billing/webhook")
async def stripe_webhook(request_body: bytes, db: Session = Depends(get_db)):
    """Handle Stripe webhook events."""
    from fastapi import Request
    payload = request_body
    sig_header = None  # Would come from request headers in real impl

    try:
        event = stripe.Event.construct_from(
            stripe.util.convert_to_stripe_object(
                stripe.util.json.loads(payload)
            ),
            stripe.api_key
        )
    except Exception as e:
        raise HTTPException(400, f"Webhook error: {str(e)}")

    if event.type == "checkout.session.completed":
        session = event.data.object
        metadata = session.get("metadata", {})
        org_id = int(metadata.get("org_id", 0))
        org = db.query(Organisation).filter(Organisation.id == org_id).first()

        if not org:
            return {"status": "org_not_found"}

        payment_type = metadata.get("type", "topup")

        if payment_type == "subscription":
            # Activate/renew subscription
            tier = SubscriptionTier(metadata.get("tier"))
            config = SUBSCRIPTION_CONFIG[tier]
            org.tier = tier
            org.status = SubscriptionStatus.ACTIVE
            org.questionnaire_limit = config["limit"]
            org.subscription_start = datetime.utcnow()
            org.subscription_end = datetime.utcnow() + timedelta(days=365)
            org.questionnaires_used = 0  # Reset counter on renewal
        else:
            # Top-up credits
            qty = int(metadata.get("qty", 1))
            org.topup_credits += qty
            purchase = TopupPurchase(
                org_id=org_id,
                qty=qty,
                price_gbp=session.get("amount_total", 0) / 100,
                stripe_payment_id=session.get("payment_intent"),
                status="completed"
            )
            db.add(purchase)

        db.commit()

    return {"status": "ok"}


@router.get("/billing/success", response_class=HTMLResponse)
async def billing_success():
    return HTMLResponse("""
    <html><head><title>Payment Successful — CybeSure</title>
    <style>body{font-family:Arial,sans-serif;background:#0b1829;color:#dce8f5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
    .box{background:#0f2035;border:1px solid #1e3a5a;padding:48px;border-radius:4px;text-align:center;max-width:480px}
    h2{color:#00d4a0;font-size:24px;margin:0 0 12px}.sub{color:#7a9cbf;font-size:14px;line-height:1.6}
    .btn{display:inline-block;margin-top:24px;background:#00c8e0;color:#0f2035;padding:12px 28px;text-decoration:none;font-weight:700;border-radius:3px}</style>
    </head><body><div class="box">
    <div style="font-size:48px;margin-bottom:16px">✅</div>
    <h2>Payment Successful</h2>
    <p class="sub">Your credits have been added to your account. You can now continue processing questionnaires.</p>
    <a href="/" class="btn">Return to SecureAnswer</a>
    </div></body></html>
    """)


@router.get("/billing/cancel", response_class=HTMLResponse)
async def billing_cancel():
    return HTMLResponse("""
    <html><head><title>Payment Cancelled — CybeSure</title>
    <style>body{font-family:Arial,sans-serif;background:#0b1829;color:#dce8f5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
    .box{background:#0f2035;border:1px solid #1e3a5a;padding:48px;border-radius:4px;text-align:center;max-width:480px}
    h2{color:#f5a623;font-size:24px;margin:0 0 12px}.sub{color:#7a9cbf;font-size:14px}
    .btn{display:inline-block;margin-top:24px;background:#00c8e0;color:#0f2035;padding:12px 28px;text-decoration:none;font-weight:700;border-radius:3px}</style>
    </head><body><div class="box">
    <div style="font-size:48px;margin-bottom:16px">❌</div>
    <h2>Payment Cancelled</h2>
    <p class="sub">No payment was taken. Return to SecureAnswer to try again.</p>
    <a href="/" class="btn">Return to SecureAnswer</a>
    </div></body></html>
    """)
