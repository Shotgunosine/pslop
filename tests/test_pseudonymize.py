from pmap_calibrate.pseudonymize import EMPLOYEE_PLACEHOLDER, Pseudonymizer, suggest_names


def test_round_trip_covers_parts_possessives_and_accents():
    p = Pseudonymizer(["Daniel Pine", "Zoë Lakey", "Zoë"])
    text = "Dr. Pine met Zoë; Zoë's thesis is due. ZOE LAKEY agreed. Pineapple is not a name."
    out = p.apply(text)
    for name in ["Pine ", "Zoë", "ZOE", "Lakey"]:
        assert name not in out
    assert "Pineapple" in out
    assert out.startswith("Dr. [PERSON_1] met [PERSON_2]; [PERSON_2]'s thesis")
    assert len(p.placeholders) == 2  # "Zoë" folds into "Zoë Lakey"


def test_employee_name():
    p = Pseudonymizer(["Daniel Pine"], employee_name="Alex Example")
    assert p.apply("Alex Example and Dr. Example") == f"{EMPLOYEE_PLACEHOLDER} and Dr. {EMPLOYEE_PLACEHOLDER}"
    assert p.placeholders["[PERSON_1]"] == "Daniel Pine"


def test_inactive_passes_text_through():
    p = Pseudonymizer()
    assert not p.active
    assert p.apply("Dr. Pine") == "Dr. Pine"


def test_suggest_names():
    text = (
        "* Rearranged MLC schedules so Drs. Lopez-Guzman and Chen (CDN) received feedback.\n"
        "* Supervised Zoë Lakey's dissertation with Dr. Brotman at the Computational Decision "
        "Neurosciences unit and University College London."
    )
    names = [name for name, _ in suggest_names(text)]
    assert names == ["Lopez-Guzman", "Chen", "Brotman", "Zoë Lakey"]
