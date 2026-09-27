"""Drug Interaction Tool tests. No LLM involved (substring matching only), so
fully deterministic against the real DB and the real seeded rules table - no
OPENAI_API_KEY needed. Requires scripts/seed_drug_interactions.py to have been
run.
"""

from app.db.models.drug_interactions import DrugInteraction
from app.db.session import SessionLocal
from app.tools.interaction_tool import check_drug_interactions

# Verified against real data before writing anything: this patient has Ibuprofen,
# lisinopril, Hydrochlorothiazide, Simvastatin, and amLODIPine all currently active.
TRIPLE_WHAMMY_PATIENT_ID = "1050cd48-cb09-1f0e-8441-4d587e8bcb2f"
NONEXISTENT_PATIENT_ID = "00000000-0000-0000-0000-000000000000"


class TestSeededRules:
    def test_rules_are_seeded(self):
        with SessionLocal() as session:
            count = session.query(DrugInteraction).count()
        assert 20 <= count <= 30  # design doc §4.3.3 target range

    def test_every_rule_has_a_valid_shape(self):
        with SessionLocal() as session:
            rules = session.query(DrugInteraction).all()
        for rule in rules:
            assert rule.severity in ("high", "moderate")
            assert len(rule.drug_groups) >= 2  # every interaction involves at least 2 drugs
            assert all(isinstance(group, list) and group for group in rule.drug_groups)
            assert rule.mechanism
            assert rule.reference


class TestCheckDrugInteractions:
    def test_nonexistent_patient_returns_no_matches(self):
        assert check_drug_interactions(NONEXISTENT_PATIENT_ID) == []

    def test_verified_triple_whammy_patient(self):
        matches = check_drug_interactions(TRIPLE_WHAMMY_PATIENT_ID)
        names = {m.name for m in matches}
        assert "Triple Whammy (NSAID + ACE inhibitor/ARB + Diuretic)" in names
        assert "Simvastatin + Amlodipine" in names

    def test_triple_whammy_match_cites_the_actual_medications(self):
        matches = check_drug_interactions(TRIPLE_WHAMMY_PATIENT_ID)
        triple_whammy = next(m for m in matches if m.name.startswith("Triple Whammy"))
        assert len(triple_whammy.matched_medications) == 3
        combined = " ".join(triple_whammy.matched_medications).lower()
        assert "ibuprofen" in combined
        assert "lisinopril" in combined
        assert "hydrochlorothiazide" in combined

    def test_matching_is_case_insensitive(self):
        # amLODIPine is stored mixed-case in this dataset - confirm the match still fires
        matches = check_drug_interactions(TRIPLE_WHAMMY_PATIENT_ID)
        statin_match = next(m for m in matches if m.name == "Simvastatin + Amlodipine")
        assert any("amlodipine" in med.lower() for med in statin_match.matched_medications)

    def test_high_severity_rules_present_in_a_real_match(self):
        matches = check_drug_interactions(TRIPLE_WHAMMY_PATIENT_ID)
        assert any(m.severity == "high" for m in matches)
