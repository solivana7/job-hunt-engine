from engine.matcher.score import score_posting

SCR = {"digest_min": 80, "instant_alert": 90, "rules_base_points": 40,
       "salary_null_cap": 79, "exception_cap": 79}

PROF = {
    "lane": "data_ai",
    "must_have": {
        "salary_floor": 240000,
        "titles_include": ["Chief Data Officer", "VP Data", "Head of AI"],
        "kill_criteria": ["requires relocation", "individual contributor only", "onsite 5 days"],
    },
    "fit_signals": {"industries": ["healthtech"], "keywords": ["data governance", "GenAI"]},
}

WELLNESS = {
    "lane": "wellness",
    "must_have": {
        "salary_floor": 150000,
        "titles_include": ["Director of Wellness"],
        "kill_criteria": ["commission only"],
    },
    "fit_signals": {"industries": ["wellness"], "keywords": ["breathwork"]},
    "exceptions": {"flagship_brands": ["Miraval"], "equity_keywords": ["equity"]},
}


def _posting(**kw):
    base = dict(company="Acme", title="Chief Data Officer", location="Remote",
                salary_min=250000, salary_max=300000,
                description="Enterprise data governance and GenAI transformation. 50,000 employees.")
    base.update(kw)
    return base


def test_passes_rules_and_scores_high():
    r = score_posting(_posting(), PROF, SCR)
    assert r["hard_pass"] is True
    assert r["score"] >= 80  # 40 base + heuristic industry/keyword/scale hits


def test_kill_on_relocation():
    r = score_posting(_posting(description="this role requires relocation to Texas"), PROF, SCR)
    assert r["hard_pass"] is False and r["score"] == 0


def test_kill_on_title_mismatch():
    r = score_posting(_posting(title="Frontend Engineer"), PROF, SCR)
    assert r["hard_pass"] is False


def test_kill_below_salary_floor():
    r = score_posting(_posting(salary_min=100000, salary_max=140000), PROF, SCR)
    assert r["hard_pass"] is False


def test_salary_null_caps_at_79():
    r = score_posting(_posting(salary_min=None, salary_max=None), PROF, SCR)
    assert r["hard_pass"] is True
    assert r["score"] <= 79


def test_onsite_5_days_kill():
    r = score_posting(_posting(description="this is an on-site 5 days per week role"), PROF, SCR)
    assert r["hard_pass"] is False


def test_wellness_flagship_exception_allows_but_caps():
    p = _posting(company="Miraval", title="Director of Wellness",
                 salary_min=110000, salary_max=130000,
                 description="Spa and wellness programming. Breathwork curriculum.")
    r = score_posting(p, WELLNESS, SCR)
    assert r["hard_pass"] is True
    assert r["score"] <= 79
    assert any("exception" in x for x in r["rationale"]["reasons"])


def test_wellness_generic_low_salary_killed():
    p = _posting(company="Generic Spa Co", title="Director of Wellness",
                 salary_min=90000, salary_max=99000)
    r = score_posting(p, WELLNESS, SCR)
    assert r["hard_pass"] is False


# --- title_groups + tiered floors ------------------------------------------

SCR_TIERED = dict(SCR, salary_floor_exec=240000, salary_floor_senior=165000)

PROF_GROUPS = {
    "lane": "data_ai",
    "must_have": {"tiered_floors": True, "kill_criteria": []},
    "title_groups": [
        {"name": "exec", "topics": ["Chief Data", "CDO"], "levels": []},
        {"name": "bi", "topics": ["Business Intelligence", "Analytics"],
         "levels": ["Senior Manager", "Director", "Vice President", "VP"]},
        {"name": "transformation",
         "topics": ["Transformation"],
         "levels": ["Senior Director", "Director", "VP"]},
    ],
    "fit_signals": {"industries": ["consulting"], "keywords": ["analytics"]},
}


def test_group_bi_vp_matches():
    r = score_posting(_posting(title="VP, Business Intelligence"), PROF_GROUPS, SCR_TIERED)
    assert r["hard_pass"] is True


def test_group_bi_director_matches_at_senior_floor():
    p = _posting(title="Director of Analytics", salary_min=170000, salary_max=200000)
    r = score_posting(p, PROF_GROUPS, SCR_TIERED)
    assert r["hard_pass"] is True  # 200k >= 165k senior floor


def test_group_bi_director_below_senior_floor_killed():
    p = _posting(title="Director of Analytics", salary_min=120000, salary_max=140000)
    r = score_posting(p, PROF_GROUPS, SCR_TIERED)
    assert r["hard_pass"] is False


def test_group_bi_vp_below_exec_floor_killed():
    p = _posting(title="VP, Business Intelligence", salary_min=180000, salary_max=220000)
    r = score_posting(p, PROF_GROUPS, SCR_TIERED)
    assert r["hard_pass"] is False  # 220k < 240k exec floor


def test_group_junior_bi_rejected():
    r = score_posting(_posting(title="Business Intelligence Analyst"), PROF_GROUPS, SCR_TIERED)
    assert r["hard_pass"] is False  # topic matches, no senior level


def test_group_transformation_director():
    r = score_posting(_posting(title="Senior Director, Digital Transformation"),
                      PROF_GROUPS, SCR_TIERED)
    assert r["hard_pass"] is True
