from keyword_engine.analyze import (Evidence, Matcher, build_queries, cluster, cluster_key,
                                    contains_word, drop_reason, intent, merge_variants, min_prefix_len,
                                    modifier, normalize, shape, stem, variant_key)

# --- Fable's July pattern tests, kept as written ---

def test_generalizes_tail_word():
    assert shape("best gravel for driveways", Matcher("gravel")) == "best {keyword} for {x}"


def test_cross_keyword_collapse():
    a = shape("best gravel for driveways", Matcher("gravel"))
    b = shape("best mulch for gardens", Matcher("mulch"))
    assert a == b


def test_consecutive_slots_collapse():
    assert shape("gravel for large modern driveways", Matcher("gravel")) == "{keyword} for {x}"


def test_num_slot():
    assert shape("gravel near me within 20 mi", Matcher("gravel")) == "{keyword} near me within {num} {x}"


# --- word boundaries ---

def test_asbestos_is_not_best():
    assert not contains_word("best", "asbestos removal cost")


def test_best_at_boundary():
    assert contains_word("best", "the best gravel")


def test_multiword():
    assert contains_word("near me", "gravel near me")


# --- matching the seed ---

def test_matcher_allows_plurals():
    m = Matcher("reef tank")
    assert m.span("reef tanks for sale") is not None
    assert m.span("best reef tank lights") is not None


def test_matcher_rejects_longer_words():
    assert Matcher("reef tank").span("reef tankers vacancies") is None


def test_synonyms_count_as_on_topic():
    m = Matcher("reef tank", ["reef aquarium"])
    assert drop_reason("reef aquarium lighting", m, []) is None


def test_drop_reasons():
    m = Matcher("reef tank")
    assert drop_reason("fish tank", m, []) == "off_topic"
    assert drop_reason("reef tank lyrics", m, ["lyrics"]) == "skip_word"
    assert drop_reason("рифовый reef tank аквариум", m, []) == "not_english"


def test_normalize():
    assert normalize("  Reef Tank’s  LIGHTS?? ") == "reef tank's lights"


# --- features ---

def test_modifier():
    m = Matcher("reef tank")
    assert modifier("reef tank lights", m) == "lights"
    assert modifier("best fish for reef tank", m) == "best fish for"


def test_light_family_clusters_together():
    m = Matcher("reef tank")
    keys = {cluster_key(p, m) for p in ["reef tank lights", "reef tank lighting", "best light for reef tank"]}
    assert keys == {"light"}


def test_general_cluster_for_bare_seed():
    assert cluster_key("best reef tanks", Matcher("reef tank")) == "(general)"


def test_no_is_not_a_cluster():
    assert cluster_key("reef tank no sump", Matcher("reef tank")) == "sump"


def test_stem():
    assert stem("supplies") == "supply"
    assert stem("lights") == "light"
    assert stem("lighting") == "light"
    assert stem("dosing") == "dosing"
    assert stem("glass") == "glass"
    assert stem("cactus") == "cactus"


def test_intent():
    assert intent("reef tank store near me") == "local"
    assert intent("best reef tank lights") == "commercial"
    assert intent("how much does a reef tank cost") == "commercial"
    assert intent("how to cycle a reef tank") == "question"
    assert intent("reef tank parameters") == "informational"


def test_query_plan():
    plan = build_queries("Reef Tank")
    assert plan[0] == ("seed", "reef tank")
    assert ("suffix", "reef tank z") in plan
    assert ("pre", "best reef tank") in plan
    assert len(plan) == len(set(q for _, q in plan))


# --- clustering and prefixes ---

def _row(phrase, engines=1):
    return {"phrase": phrase, "engine_count": engines, "intent": "informational"}


def test_cluster_heads_are_top_ranked_member():
    rows = [_row("reef tank lights", 5), _row("reef tank lighting", 2), _row("reef tank salinity", 4)]
    clusters = cluster(rows, Matcher("reef tank"))
    light = next(c for c in clusters if c["key"] == "light")
    assert light["head"] == "reef tank lights" and light["size"] == 2
    assert all(r["cluster_id"] for r in rows)


def test_variant_key_merges_order_plurals_and_filler():
    assert variant_key("nano reef tank") == variant_key("reef tank nano")
    assert variant_key("reef tank corals") == variant_key("corals for reef tank")
    assert variant_key("reef tank setups") == variant_key("reef tank setup")
    assert variant_key("reef tank with sump") == variant_key("reef tank sump")
    assert variant_key("reef tank without sump") != variant_key("reef tank sump")
    assert variant_key("best reef tank lights") != variant_key("reef tank lights")


def test_merge_variants_pools_evidence():
    a, b = Evidence("reef tank nano"), Evidence("nano reef tank")
    a.add("google", "suffix", "reef tank n", {"rank": 3, "relevance": 600})
    a.add("bing", "suffix", "reef tank n", {"rank": 2})
    b.add("amazon", "pre", "nano reef tank", {"rank": 1})
    merged = merge_variants({a.phrase: a, b.phrase: b})
    assert list(merged) == ["reef tank nano"]  # the wording with more evidence of its own leads
    pooled, variants = merged["reef tank nano"]
    assert pooled.engines == {"google", "bing", "amazon"} and pooled.best_rank == 1
    assert pooled.google_relevance == 600 and variants == ["reef tank nano", "nano reef tank"]
    assert a.engines == {"google", "bing"}  # originals untouched


def test_min_prefix_len():
    target = "reef tank lights"

    def suggest(q):  # shows from 7 typed characters on, but never for the full phrase (like Google)
        return [{"phrase": target}] if 7 <= len(q) < len(target) else [{"phrase": "reef"}]

    assert min_prefix_len(target, suggest) == 7


def test_min_prefix_none_when_never_suggested():
    assert min_prefix_len("reef tank lights", lambda q: []) is None
