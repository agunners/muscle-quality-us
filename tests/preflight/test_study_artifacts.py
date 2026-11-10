"""Tests for the zip artifact counter — validated against the REAL anonacl pilot zip layout."""
from preflight_helpers.study_artifacts import count_artifacts

STUDY = "07jun06_093_anonabr_anonaab_49e_179_82"


def _real_anonacl_namelist():
    """Reproduces the structure observed in the real 263MB anonacl zip: 8 captures
    (8 mp4 + 8 RawImages folders + 8 dcm) PLUS 2 '.trashed-' mp4 left by deleted
    re-shoots that got zipped in."""
    names = [f"{STUDY}/"]
    stamps = ["112554", "112714", "112754", "113023", "113220", "113359", "113529", "115753"]
    for i, ts in enumerate(stamps, 1):
        names.append(f"{STUDY}/{STUDY}_20260608_{ts}.mp4")
        names.append(f"{STUDY}/{STUDY}_20260608_{ts}_{i}.dcm")
        folder = f"{STUDY}/RawImagesForResearch_20260608_{ts}001"
        names.append(folder + "/")
        names.append(folder + "/frame_0001.png")
        names.append(folder + "/frame_0002.jpg")
    # device-trash leftovers (must be excluded)
    names.append(f"{STUDY}/.trashed-1783500713-{STUDY}_20260608_112151.mp4")
    names.append(f"{STUDY}/.trashed-1783501293-{STUDY}_20260608_112151.mp4")
    return names


def test_real_anonacl_counts_8_8_8_excluding_trash():
    c = count_artifacts(_real_anonacl_namelist())
    assert (c.mp4, c.rawimages, c.dcm) == (8, 8, 8)
    assert c.as_str() == "8/8/8"
    assert c.excluded == 2  # the two .trashed- mp4


def test_clean_main_cohort_6_6_6():
    names = [f"{STUDY}/"]
    for i, ts in enumerate(["a", "b", "c", "d", "e", "f"], 1):
        names += [
            f"{STUDY}/clip_{ts}.mp4",
            f"{STUDY}/still_{ts}_{i}.dcm",
            f"{STUDY}/RawImagesForResearch_{ts}/x.png",
        ]
    c = count_artifacts(names)
    assert c.as_str() == "6/6/6"
    assert c.excluded == 0


def test_macosx_and_dsstore_excluded():
    names = [
        f"{STUDY}/a.mp4", f"{STUDY}/a_1.dcm", f"{STUDY}/RawImagesForResearch_a/x.png",
        "__MACOSX/._a.mp4", f"{STUDY}/.DS_Store",
    ]
    c = count_artifacts(names)
    assert c.as_str() == "1/1/1"


def test_trashed_rawimages_folder_not_counted():
    names = [
        f"{STUDY}/a.mp4", f"{STUDY}/a_1.dcm",
        f"{STUDY}/RawImagesForResearch_a/x.png",
        f"{STUDY}/.trashed-99-RawImagesForResearch_b/y.png",
    ]
    c = count_artifacts(names)
    assert c.rawimages == 1
