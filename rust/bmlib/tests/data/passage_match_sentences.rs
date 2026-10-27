// Included by `tests/passage_match.rs` and by the crate's own
// `citations::passage_match` unit tests, which count the windows scored (#533).
// The abstract of `tests/test_passage_match.py`'s `SENTENCES`.
const SENTENCES: [&str; 7] = [
    "Background: Atrial fibrillation is the most common sustained arrhythmia and is \
     associated with a fivefold increase in the risk of stroke.",
    "Methods: We conducted a randomised controlled trial in 1,204 patients aged 65 \
     years or older across 14 centres in Europe between 2015 and 2019.",
    "Patients were assigned to apixaban 5 mg twice daily or to warfarin with a target \
     international normalised ratio of 2.0 to 3.0.",
    "The primary outcome was stroke or systemic embolism; the primary safety outcome \
     was major bleeding according to ISTH criteria.",
    "Results: Over a median follow-up of 2.1 years, stroke or systemic embolism \
     occurred in 1.27% per year in the apixaban group and 1.60% per year in the \
     warfarin group (hazard ratio 0.79; 95% CI 0.66 to 0.95).",
    "Major bleeding occurred in 2.13% per year with apixaban versus 3.09% per year \
     with warfarin.",
    "Conclusions: In elderly patients with atrial fibrillation, apixaban was superior \
     to warfarin in preventing stroke or systemic embolism, caused less bleeding and \
     resulted in lower mortality.",
];
