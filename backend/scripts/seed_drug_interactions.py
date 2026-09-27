"""Seed the hand-curated drug interaction rules table (design doc §4.3.3).

Every drug name pattern below was verified present in medications.DESCRIPTION
in this actual dataset before being written (see the session that built this -
distinct medication descriptions were pulled and cross-checked) - these aren't
theoretical rules that happen to never match anything.

These are well-established, textbook-level drug-drug interactions (the kind
covered in any clinical pharmacology reference), not exotic or disputed claims -
each mechanism/reference line is a real, defensible clinical fact, matching the
design doc's own bar ("every row can be explained and defended").

Safe to re-run - truncates and reloads (small, fully-owned table, not a
concern the way truncating a Synthea table would be).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models.drug_interactions import DrugInteraction
from app.db.session import SessionLocal

RULES = [
    # --- Nephrotoxicity cluster (anchor scenario: "why did creatinine double") ---
    dict(
        name="Triple Whammy (NSAID + ACE inhibitor/ARB + Diuretic)",
        drug_groups=[["ibuprofen", "naproxen"], ["lisinopril", "losartan"], ["hydrochlorothiazide", "furosemide"]],
        severity="high",
        mechanism="NSAIDs reduce renal prostaglandin-mediated afferent arteriolar dilation, ACE inhibitors/ARBs "
                  "reduce efferent arteriolar tone, and diuretics cause volume depletion - together they can "
                  "sharply reduce glomerular filtration pressure and precipitate acute kidney injury.",
        reference="Well-documented pattern in nephrology and primary-care literature (Loboz & Shenfield 2005, "
                   "'Drug combinations and impairment of renal function').",
    ),
    dict(
        name="NSAID + ACE inhibitor/ARB",
        drug_groups=[["ibuprofen", "naproxen"], ["lisinopril", "losartan"]],
        severity="moderate",
        mechanism="NSAIDs blunt the renal vasodilation ACE inhibitors/ARBs depend on, reducing antihypertensive "
                   "efficacy and raising acute kidney injury risk even without a diuretic present.",
        reference="Component interaction of the triple whammy pattern; well documented independently.",
    ),
    dict(
        name="NSAID + Diuretic",
        drug_groups=[["ibuprofen", "naproxen"], ["hydrochlorothiazide", "furosemide"]],
        severity="moderate",
        mechanism="NSAIDs cause sodium/fluid retention that reduces diuretic efficacy, and combined with "
                   "diuretic-induced volume depletion can reduce renal perfusion.",
        reference="Component interaction of the triple whammy pattern; well documented independently.",
    ),
    dict(
        name="NSAID + Tacrolimus",
        drug_groups=[["ibuprofen", "naproxen"], ["tacrolimus"]],
        severity="high",
        mechanism="Both NSAIDs and calcineurin inhibitors (tacrolimus) are independently nephrotoxic; combined "
                   "use compounds the risk of acute kidney injury, particularly relevant in transplant patients.",
        reference="Established transplant-pharmacology concern; calcineurin inhibitors carry a nephrotoxicity "
                   "black-box-level caution independent of NSAID co-administration.",
    ),
    dict(
        name="ACE inhibitor + ARB (dual RAAS blockade)",
        drug_groups=[["lisinopril"], ["losartan"]],
        severity="moderate",
        mechanism="Combining an ACE inhibitor and an ARB doubly blocks the renin-angiotensin system, increasing "
                   "risk of hyperkalemia and acute kidney injury without added cardiovascular benefit.",
        reference="ONTARGET trial (2008) - dual RAAS blockade increased renal adverse events without outcome benefit.",
    ),
    dict(
        name="Tacrolimus + Diuretic",
        drug_groups=[["tacrolimus"], ["hydrochlorothiazide", "furosemide"]],
        severity="moderate",
        mechanism="Diuretic-induced volume depletion reduces renal perfusion, worsening calcineurin-inhibitor "
                   "(tacrolimus) nephrotoxicity.",
        reference="Established transplant-pharmacology concern around calcineurin inhibitor dosing and volume status.",
    ),
    # --- Statin + CYP3A4 inhibitor myopathy cluster ---
    dict(
        name="Simvastatin + Amlodipine",
        drug_groups=[["simvastatin"], ["amlodipine"]],
        severity="moderate",
        mechanism="Amlodipine inhibits CYP3A4-mediated simvastatin metabolism, raising statin plasma levels and "
                   "myopathy/rhabdomyolysis risk.",
        reference="FDA drug safety communication (2011) - simvastatin dose capped at 20mg/day with amlodipine.",
    ),
    dict(
        name="Simvastatin + Verapamil",
        drug_groups=[["simvastatin"], ["verapamil"]],
        severity="moderate",
        mechanism="Verapamil inhibits CYP3A4-mediated simvastatin metabolism, raising statin plasma levels and "
                   "myopathy/rhabdomyolysis risk.",
        reference="FDA drug safety communication (2011) - simvastatin dose capped at 10mg/day with verapamil.",
    ),
    dict(
        name="Simvastatin + Tacrolimus",
        drug_groups=[["simvastatin"], ["tacrolimus"]],
        severity="moderate",
        mechanism="Tacrolimus can inhibit CYP3A4-mediated statin metabolism, increasing exposure and myopathy risk.",
        reference="Recognized transplant-pharmacology drug interaction; monitor for statin-related myopathy.",
    ),
    # --- Bleeding risk cluster ---
    dict(
        name="Warfarin + NSAID",
        drug_groups=[["warfarin"], ["ibuprofen", "naproxen"]],
        severity="high",
        mechanism="NSAIDs impair platelet function and can displace warfarin from protein binding, compounding "
                   "bleeding risk on top of anticoagulation.",
        reference="Well-established, widely taught anticoagulant-NSAID interaction.",
    ),
    dict(
        name="Warfarin + Aspirin",
        drug_groups=[["warfarin"], ["aspirin"]],
        severity="high",
        mechanism="Combining an anticoagulant with an antiplatelet agent substantially increases bleeding risk.",
        reference="Well-established combined antithrombotic bleeding-risk interaction.",
    ),
    dict(
        name="Warfarin + Clopidogrel",
        drug_groups=[["warfarin"], ["clopidogrel"]],
        severity="high",
        mechanism="Combining an anticoagulant with an antiplatelet agent substantially increases bleeding risk.",
        reference="Well-established combined antithrombotic bleeding-risk interaction.",
    ),
    dict(
        name="Warfarin + Amoxicillin",
        drug_groups=[["warfarin"], ["amoxicillin"]],
        severity="moderate",
        mechanism="Amoxicillin (particularly with clavulanate) can potentiate warfarin's anticoagulant effect, "
                   "likely via gut flora disruption affecting vitamin K synthesis, raising INR and bleeding risk.",
        reference="Recognized antibiotic-warfarin interaction; INR monitoring recommended when co-prescribed.",
    ),
    dict(
        name="Warfarin + Enoxaparin",
        drug_groups=[["warfarin"], ["enoxaparin"]],
        severity="high",
        mechanism="Concurrent full-dose anticoagulation from two different agents (typically only intended "
                   "transiently as bridging therapy) substantially increases bleeding risk if continued.",
        reference="Standard anticoagulation-bridging safety concern.",
    ),
    dict(
        name="Clopidogrel + Aspirin",
        drug_groups=[["clopidogrel"], ["aspirin"]],
        severity="moderate",
        mechanism="Dual antiplatelet therapy increases bleeding risk relative to either agent alone; sometimes "
                   "intentional post-cardiac-stent, but still a risk factor worth flagging.",
        reference="Well-established dual-antiplatelet-therapy bleeding-risk profile.",
    ),
    dict(
        name="Clopidogrel + Enoxaparin",
        drug_groups=[["clopidogrel"], ["enoxaparin"]],
        severity="moderate",
        mechanism="Combining an antiplatelet agent with an anticoagulant increases bleeding risk.",
        reference="Well-established combined antithrombotic bleeding-risk interaction.",
    ),
    # --- Cardiac conduction cluster ---
    dict(
        name="Digoxin + Verapamil",
        drug_groups=[["digoxin"], ["verapamil"]],
        severity="high",
        mechanism="Verapamil reduces digoxin clearance (raising digoxin levels toward toxicity) and both slow AV "
                   "nodal conduction, compounding bradycardia/heart-block risk.",
        reference="Well-established, widely taught digoxin-verapamil interaction.",
    ),
    dict(
        name="Digoxin + Diuretic",
        drug_groups=[["digoxin"], ["furosemide", "hydrochlorothiazide"]],
        severity="high",
        mechanism="Diuretic-induced hypokalemia increases myocardial sensitivity to digoxin, raising risk of "
                   "digoxin toxicity (arrhythmia) even at therapeutic digoxin levels.",
        reference="Well-established, widely taught electrolyte-mediated digoxin toxicity risk.",
    ),
    dict(
        name="Metoprolol + Verapamil",
        drug_groups=[["metoprolol"], ["verapamil"]],
        severity="high",
        mechanism="A beta-blocker and a non-dihydropyridine calcium channel blocker both slow AV nodal "
                   "conduction; combined use risks additive bradycardia or heart block.",
        reference="Well-established, widely taught additive-AV-block interaction.",
    ),
    dict(
        name="Digoxin + Metoprolol",
        drug_groups=[["digoxin"], ["metoprolol"]],
        severity="moderate",
        mechanism="Both agents slow heart rate via different mechanisms; combined use risks additive bradycardia.",
        reference="Recognized additive-bradycardia combination, generally monitored rather than avoided.",
    ),
]


def main() -> None:
    session = SessionLocal()
    try:
        session.query(DrugInteraction).delete()
        for rule in RULES:
            session.add(DrugInteraction(
                name=rule["name"],
                drug_groups=rule["drug_groups"],
                severity=rule["severity"],
                mechanism=rule["mechanism"],
                reference=rule["reference"],
                created_at=datetime.now(timezone.utc),
            ))
        session.commit()
        print(f"Seeded {len(RULES)} drug interaction rules.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
