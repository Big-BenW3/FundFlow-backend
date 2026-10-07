"""Seed the hackathon demo (PRODUCT.md §67).

Run:  uv run python seed.py

Creates:
* demo@fundflow.app / password12345  (campaign owner)
* four contributor accounts (same password)
* "Build A Community Health Center" — ₦10M target, ₦7.5M raised,
  2 verified beneficiaries, 4 milestones (2 approved + paid),
  full ledger + audit trail through the real service/webhook pipeline
* a second public campaign + one PRIVATE campaign (privacy demo)
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

from app.core.config import get_settings
from app.core.database import get_session_factory, init_db
from app.core.security import hash_password
from app.integrations.kora import get_kora_client
from app.models.beneficiary import Beneficiary
from app.models.campaign import Campaign, CampaignUpdate
from app.models.milestone import Milestone
from app.models.user import User
from app.services import campaign_service, contribution_service, milestone_service, payout_service
from app.services.activity import log_audit

PASSWORD = "password12345"
CONTRIBUTIONS = [
    ("chidi@example.com", "Chidi Eze", "2000000", False),
    ("fatima@example.com", "Fatima Bello", "1500000", True),
    ("tunde@example.com", "Tunde Adeleke", "1000000", False),
    ("ngozi@example.com", "Ngozi Obi", "3000000", False),
]


DEMO_CAMPAIGNS = [
    {
        "title": "Supply Clean Drinking Water to 5 Schools",
        "short_description": "Borehole and purification units for 5 rural schools.",
        "description": (
            "Five schools in a drought-affected district share one borehole "
            "that runs dry for months. Funds buy a solar-powered borehole, "
            "filtration units and a maintenance fund, delivered in two stages."
        ),
        "category": "Community",
        "visibility": "PUBLIC",
        "target_amount": 8_000_000,
        "deadline_days": 30,
        "cover_image": (
            "https://images.unsplash.com/photo-1469474968028-56623f02e42e"
            "?auto=format&fit=crop&w=1600&q=80"
        ),
        "contributors": [
            ("adaeze@example.com", "Adaeze Okonkwo", "1500000", False),
            ("emeka@example.com", "Emeka Nwosu", "2200000", True),
            ("chinwe@example.com", "Chinwe Eze", "900000", False),
        ],
        "beneficiaries": [("Clean Water Schools Trust", "033"), ("Village Water Aid", "057")],
        "milestone_specs": [
            ("Solar-powered borehole drilled", "Pump, piping and hand-pump installed.", "3500000", "APPROVAL"),
            ("Purification units deployed", "Bacterial and nitrate testing completed.", "2000000", "DATE"),
        ],
    },
    {
        "title": "Provide Solar Power for a Rural Clinic",
        "short_description": "Solar panels and battery storage for a 24-hour clinic.",
        "description": (
            "The clinic in Umuoba runs on a noisy, fuel-hungry generator "
            "only a few hours a day. Solar panels, inverters and battery "
            "banks keep the vaccine fridge running around the clock."
        ),
        "category": "Medical",
        "visibility": "PUBLIC",
        "target_amount": 12_000_000,
        "deadline_days": 45,
        "cover_image": (
            "https://images.unsplash.com/photo-1505740420928-5e560c06d30e"
            "?auto=format&fit=crop&w=1600&q=80"
        ),
        "contributors": [
            ("chukwudi@example.com", "Chukwudi Okonkwo", "2000000", False),
            ("nkechi@example.com", "Nkechi Uche", "2000000", True),
            ("jude@example.com", "Jude Obi", "2000000", False),
        ],
        "beneficiaries": [("Umuoba Community Clinic", "044"), ("Ignis Medical Supply", "033")],
        "milestone_specs": [
            ("Solar array + inverter installation", "30 panels, 40kWh battery bank.", "6000000", "APPROVAL"),
            ("Vaccine fridge + lab lights", "Cold-chain equipment for the maternity ward.", "2000000", "DATE"),
        ],
    },
    {
        "title": "Fund a Maternal Health Outreach",
        "short_description": "A mobile clinic for mothers and newborns in the north.",
        "description": (
            "Pregnant women in a remote northern district travel 40 km to "
            "reach a clinic. The outreach brings antenatal checks, deliveries "
            "and postnatal care to three community hubs."
        ),
        "category": "Medical",
        "visibility": "PUBLIC",
        "target_amount": 10_000_000,
        "deadline_days": 35,
        "cover_image": (
            "https://images.unsplash.com/photo-1580273817418-c0f7e82000b8"
            "?auto=format&fit=crop&w=1600&q=80"
        ),
        "contributors": [
            ("halima@example.com", "Halima Musa", "2500000", False),
            ("ismael@example.com", "Ismael Bello", "1500000", False),
            ("fatima@example.com", "Fatima Bello", "1200000", False),
        ],
        "beneficiaries": [("Northern Maternal Health Trust", "001"), ("Safe Delivery Initiative", "035")],
        "milestone_specs": [
            ("Mobile clinic vehicle + equipment", "Pickup, exam bay and lab kit.", "5000000", "APPROVAL"),
            ("Antenatal supplies delivered", "Vitamins, iron and delivery kits.", "2500000", "DATE"),
        ],
    },
    {
        "title": "Enable School Notebooks for 5 Rural Schools",
        "short_description": "Notebooks, pens and backpacks for 5 schools.",
        "description": (
            "Five rural schools are out of notebooks. Funds send 5,000 "
            "books, pens and backpacks, packed by the children themselves."
        ),
        "category": "Community",
        "visibility": "PUBLIC",
        "target_amount": 5_000_000,
        "deadline_days": 30,
        "cover_image": (
            "https://images.unsplash.com/photo-1521587760399-64633f01c6e5"
            "?auto=format&fit=crop&w=1600&q=80"
        ),
        "contributors": [
            ("uche@example.com", "Uche Nwosu", "1500000", False),
            ("chioma@example.com", "Chioma Eze", "1500000", False),
            ("zainab@example.com", "Zainab Mohammed", "1000000", True),
        ],
        "beneficiaries": [("Primary School St. Joseph", "033"), ("Mama Miti Charity", "058")],
        "milestone_specs": [
            ("Order + pack exercise books", "5,000 books, pens and backpacks sourced.", "2500000", "APPROVAL"),
            ("Distribution to all pupils", "Children receive full school supplies.", "1500000", "DATE"),
        ],
    },
]
def get_or_create_user(db, email: str, name: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(PASSWORD),
        role="USER",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def add_contribution(db, campaign: User | Campaign, user: User, amount: str, anonymous: bool):
    db.commit()  # release locks — the mock webhook opens its own session
    from app.models.campaign import Campaign as C

    campaign = db.get(C, campaign.id)
    db.expire_all()
    contribution_service.initiate_contribution(
        db,
        campaign=campaign,
        user=user,
        amount=Decimal(amount),
        anonymous=anonymous,
        background_tasks=None,  # synchronous delivery
    )
    db.expire_all()


def seed_main_campaign(db, owner: User) -> Campaign:
    from datetime import datetime, timezone

    from app.core.database import get_session_factory as _sf  # noqa: F401

    campaign = campaign_service.create_campaign(
        db,
        owner,
        {
            "title": "Build A Community Health Center",
            "short_description": "A 40-bed health centre for Orlu rural communities — built in verified milestones.",
            "description": (
                "Orlu LGA currently has one clinic for over 40,000 people. "
                "This campaign funds a community health centre in four verified "
                "stages: foundation, structure, medical equipment and completion. "
                "Every naira is tracked in a public ledger, and funds are released "
                "to verified beneficiaries only when each milestone is approved.\n\n"
                "Kora powers the collection account, transaction verification and "
                "every payout."
            ),
            "category": "Community",
            "visibility": "PUBLIC",
            "target_amount": 10_000_000,
            "deadline": datetime.now(timezone.utc) + timedelta(days=30),
            "cover_image": (
                "https://images.unsplash.com/photo-1519494026892-80bbd2d6fd0d"
                "?auto=format&fit=crop&w=1600&q=80"
            ),
            "is_diaspora": False,
        },
    )
    campaign_service.ensure_collection_account(db, campaign, owner)

    abc = Beneficiary(
        campaign_id=campaign.id,
        name="ABC Construction Ltd",
        bank_code="033",
        bank_name="UBA",
        account_number="1234567890",
        resolved_account_name="JOHN OKAFOR",
        verification_status="VERIFIED",
    )
    equipment = Beneficiary(
        campaign_id=campaign.id,
        name="Medical Equipment Ltd",
        bank_code="058",
        bank_name="Guaranty Trust Bank (GTB)",
        account_number="0123456789",
        resolved_account_name="JOHN MICHAEL DOE",
        verification_status="VERIFIED",
    )
    db.add_all([abc, equipment])
    db.commit()
    db.refresh(abc)

    milestone_specs = [
        ("Foundation", "Excavation, blinding and foundation slab completed.", "2000000", abc.id),
        ("Structure", "Load-bearing walls, columns and decking completed.", "3000000", abc.id),
        ("Medical Equipment", "Delivery of beds, diagnostic tools and theatre kit.", "3000000", equipment.id),
        ("Final Completion", "Finishing, water, power and handover.", "2000000", equipment.id),
    ]
    milestones: list[Milestone] = []
    for index, (name, description, amount, beneficiary_id) in enumerate(milestone_specs, start=1):
        milestone = Milestone(
            campaign_id=campaign.id,
            order_index=index,
            name=name,
            description=description,
            amount=Decimal(amount),
            beneficiary_id=beneficiary_id,
            trigger_type="APPROVAL",
            requires_approval=True,
        )
        db.add(milestone)
        milestones.append(milestone)
    db.commit()
    for m in milestones:
        db.refresh(m)

    return campaign



def seed_demo_campaigns(db, owner: User) -> list[Campaign]:
    """Seed 5 demo campaigns with full mock Kora coverage.

    Each campaign follows the complete lifecycle:
    Campaign -> CollectionAccount -> 3 contributors (mock charge + webhook)
    -> 2 verified beneficiaries -> 2 milestones (1 APPROVAL-gated, 1 DATE-gated)
    -> approve the APPROVAL milestone -> execute payout (mock transfer).
    """
    from datetime import datetime, timezone

    from app.integrations.kora.mock import _bank_name

    kora = get_kora_client()
    created: list[Campaign] = []

    baseline_accounts = len(kora._mock.accounts)
    baseline_charges = len(kora._mock.charges)
    baseline_transfers = len(kora._mock.transfers)

    for spec in DEMO_CAMPAIGNS:
        campaign = campaign_service.create_campaign(
            db,
            owner,
            {
                "title": spec["title"],
                "short_description": spec["short_description"],
                "description": spec["description"],
                "category": spec["category"],
                "visibility": spec["visibility"],
                "target_amount": spec["target_amount"],
                "deadline": datetime.now(timezone.utc) + timedelta(days=spec["deadline_days"]),
                "cover_image": spec["cover_image"],
                "is_diaspora": False,
            },
        )
        campaign_service.ensure_collection_account(db, campaign, owner)

        # --- Contributors (mock charge registration + webhook processing) ---
        for (email, name, amount, anonymous) in spec["contributors"]:
            user = get_or_create_user(db, email, name)
            add_contribution(db, campaign, user, amount, anonymous)
            print(
                f"  - contribution NGN {int(amount):,} from {email} "
                f"(anonymous={anonymous})"
            )
        db.expire_all()

        # --- Verified beneficiaries ---
        beneficiaries = []
        for (name, bank_code) in spec["beneficiaries"]:
            beneficiary = Beneficiary(
                campaign_id=campaign.id,
                name=name,
                bank_code=bank_code,
                bank_name=_bank_name(bank_code),
                account_number=str(
                    100_000_000
                    + int(sha256(f"{name}{campaign.id}".encode()).hexdigest()[:9], 16)
                    % 899_999_999
                ).zfill(10),
                resolved_account_name=name.upper().replace(" ", ""),
                verification_status="VERIFIED",
            )
            db.add(beneficiary)
            beneficiaries.append(beneficiary)
        db.commit()
        db.expire_all()

        # --- Milestones (1 APPROVAL-gated + 1 DATE-gated) ---
        milestones = []
        for index, (name, description, amount, trigger_type) in enumerate(
            spec["milestone_specs"], start=1
        ):
            milestone = Milestone(
                campaign_id=campaign.id,
                order_index=index,
                name=name,
                description=description,
                amount=Decimal(amount),
                beneficiary_id=beneficiaries[0].id,
                trigger_type=trigger_type,
                requires_approval=(trigger_type == "APPROVAL"),
                threshold_amount=(
                    Decimal(amount) * Decimal("0.6")
                    if trigger_type == "APPROVAL"
                    else None
                ),
                due_date=(datetime.now(timezone.utc) + timedelta(days=14)),
            )
            db.add(milestone)
            milestones.append(milestone)
        db.commit()
        db.expire_all()

        # --- Approve the APPROVAL milestone + execute payout (mock transfer) ---
        approval_ms = next(
            (m for m in milestones if m.trigger_type == "APPROVAL"), None
        )
        if approval_ms is not None:
            payout = milestone_service.approve_milestone(
                db, milestone=approval_ms, campaign=campaign, actor=owner
            )
            payout_service.execute_payout(
                db,
                payout=payout,
                campaign=campaign,
                actor=owner,
                background_tasks=None,
            )
            print(
                f"  - milestone '{approval_ms.name}' approved + paid "
                f"(NGN {float(approval_ms.amount):,.2f})"
            )
        db.expire_all()

        # --- Stats ---
        campaign = db.get(Campaign, campaign.id)
        stats = campaign_service.dashboard_stats(db, campaign)
        print(
            f"  - '{campaign.title}' -> {campaign.slug} "
            f"(raised NGN {stats['raised']:,.2f} / {stats['target']:,.2f}, "
            f"progress {stats['progress']}%, "
            f"contributors {stats['contributors']}, "
            f"beneficiaries {len(beneficiaries)})"
        )
        created.append(campaign)

    added_accounts = len(kora._mock.accounts) - baseline_accounts
    added_charges = len(kora._mock.charges) - baseline_charges
    added_transfers = len(kora._mock.transfers) - baseline_transfers
    verified_transfers = sum(
        1 for t in kora._mock.transfers.values() if t.get("status") == "success"
    )
    print(
        f"\nSeeded {len(created)} demo campaigns. "
        f"Mock coverage: +{added_accounts} virtual account(s), "
        f"+{added_charges} charge(s) registered, "
        f"+{added_transfers} transfer(s) created, "
        f"{verified_transfers} verified transfer(s)."
    )
    return created



def main() -> None:
    from datetime import datetime, timezone

    settings = get_settings()
    print(f"Seeding: DATABASE_URL={settings.database_url}  KORA_MODE={settings.kora_mode}")
    init_db()
    db = get_session_factory()()

    if db.query(User).filter(User.email == "demo@fundflow.app").first():
        print("Demo data already present — nothing to do. (Delete fundflow.db to reseed.)")
        db.close()
        return

    owner = get_or_create_user(db, "demo@fundflow.app", "Adaeze Okonkwo")
    contributors = [
        get_or_create_user(db, email, name)
        for email, name in [
            ("chidi@example.com", "Chidi Eze"),
            ("fatima@example.com", "Fatima Bello"),
            ("tunde@example.com", "Tunde Adeleke"),
            ("ngozi@example.com", "Ngozi Obi"),
        ]
    ]

    # --- Main demo campaign -------------------------------------------
    campaign = seed_main_campaign(db, owner)
    print(f"  - campaign '{campaign.title}' -> {campaign.slug}")

    for (email, _name, amount, anonymous), user in zip(CONTRIBUTIONS, contributors):
        add_contribution(db, campaign, user, amount, anonymous)
        print(f"  - contribution NGN {int(amount):,} from {email} (anonymous={anonymous})")

    db.expire_all()
    campaign = db.get(Campaign, campaign.id)
    stats = campaign_service.dashboard_stats(db, campaign)
    print(f"  - raised NGN {stats['raised']:,.2f} / NGN {stats['target']:,.2f} "
          f"({stats['progress']}%)")

    # Approve + execute the first two milestones (full §27 flow) --------
    milestones = (
        db.query(Milestone)
        .filter(Milestone.campaign_id == campaign.id)
        .order_by(Milestone.order_index.asc())
        .all()
    )
    for milestone in milestones[:2]:
        payout = milestone_service.approve_milestone(
            db, milestone=milestone, campaign=campaign, actor=owner
        )
        campaign = db.get(Campaign, campaign.id)
        payout_service.execute_payout(
            db, payout=payout, campaign=campaign, actor=owner, background_tasks=None
        )
        print(f"  - milestone '{milestone.name}' approved + paid "
              f"(NGN {float(milestone.amount):,.2f})")

    # A progress update tied to the finished foundation (§36) ----------
    db.add(
        CampaignUpdate(
            campaign_id=campaign.id,
            milestone_id=milestones[0].id,
            body=(
                "The foundation slab was cast on schedule and independently "
                "certified by a structural engineer."
            )
        )
    )


    # --- Demo campaign seed (full mock pipeline, 5 campaigns) -------
    seed_demo_campaigns(db, owner)
    print()

    log_audit(
        db,
        campaign_id=campaign.id,
        action="update.posted",
        description="Update posted: Foundation completed",
    )
    db.commit()

    # --- Secondary public campaign ------------------------------------
    second = campaign_service.create_campaign(
        db,
        owner,
        {
            "title": "Rebuild the Community Water System",
            "short_description": "Repair borehole, pipes and storage for 3,000 residents.",
            "description": (
                "The Eke community borehole has been non-functional for 14 months. "
                "Funds are released against procurement, installation and "
                "commissioning milestones."
            ),
            "category": "Charity",
            "visibility": "PUBLIC",
            "target_amount": 10_000_000,
            "deadline": datetime.now(timezone.utc) + timedelta(days=60),
        },
    )
    campaign_service.ensure_collection_account(db, second, owner)
    add_contribution(db, second, contributors[0], "800000", False)
    add_contribution(db, second, contributors[3], "1600000", False)
    print(f"  - campaign '{second.title}' -> {second.slug}")

    # --- Private campaign (privacy demo, §29) -------------------------
    third = campaign_service.create_campaign(
        db,
        owner,
        {
            "title": "Family Medical Fund for Sarah",
            "short_description": "Private family fundraiser for heart surgery.",
            "description": "Only invited family members can view this campaign.",
            "category": "Medical",
            "visibility": "PRIVATE",
            "target_amount": 8_000_000,
            "deadline": datetime.now(timezone.utc) + timedelta(days=45),
        },
    )
    campaign_service.ensure_collection_account(db, third, owner)
    print(f"  - campaign '{third.title}' -> {third.slug} (PRIVATE)")

    kora = get_kora_client()
    print(
        f"\nSeeded. Kora mode: {'mock' if kora.is_mock else 'live'} - "
        "log in as demo@fundflow.app / password12345"
    )
    db.close()


if __name__ == "__main__":
    main()

