> **Status: SUPERSEDED 2026-05-20.** This brainstorm was the original source material for the RF frame QA/QC initiative. Its load-bearing content — the 6-step build order and the A–G detector-class taxonomy — is now absorbed into [`rf_qa_canonical_plan_v0_1.md`](rf_qa_canonical_plan_v0_1.md), the single canonical RF Image-Quality QA/QC plan (2026-05-20), which also supersedes the intermediate v0 contract. Preserved here as the drafting trail and historical context, *not* as current specification. This brainstorm is read-only going forward; refinements happen in the canonical plan via its § 12 discipline.

---

# RF Ultrasound Frame QA/QC — Brainstorm (EN / TR)

Brainstorm on quality assurance for RF (rectus femoris) ultrasound frames, scoped to the **assessment phase** — "will I be able to extract my measurements from this frame?", not muscle health. Grounded in the `muscle_mania` pipeline.

*File paths below are relative to the `muscle_mania` repo root. This document contains an English section followed by a Turkish translation.*

---

# English

## Framing: a frame is good *for* a measurement, not good in the abstract

"A passing frame" is not a property of the frame — it is a property of the frame **relative to a measurement**. The assessment phase extracts ~11 things (`compute_all_metrics`, [`labeling_app/measurements.py:1190`](../labeling_app/measurements.py#L1190)): `rf_thickness_mm`, `vi_thickness_mm`, `qmt_mm`, `rf_area_mm2`, `vi_area_mm2`, `rf_echo`, `vi_echo`, `muscle_fat_ratio`, `star_ratio`, `full_thickness_q50_mm`, `int_cv_within`. These depend on *different* image structures, so they fail *independently*. A frame can be perfect for `rf_echo` and worthless for `qmt_mm`. That fact drives the whole recommendation.

## 1. Visual cues of a passing RF frame

Organized by the structure each measurement leans on:

| Structure | Measurements that need it | What "good" looks like |
|---|---|---|
| Superficial + deep RF fascia (aponeuroses) | `rf_thickness`, `rf_area`, `full_thickness_q50`, `qmt` | Two crisp, continuous, near-horizontal echogenic lines spanning the **full image width** — no fade-out segments, no anisotropic dropout |
| RF/VI interface (deep aponeurosis) | `vi_thickness`, `vi_area`, RF–VI separation | Distinguishable as its own bright interface, not blurred into one muscle mass |
| Femur cortex + acoustic shadow | `qmt_mm`, `star_ratio` | Bright, continuous, near-horizontal cortical line with a **clean dark shadow beneath**, sitting in the central zone of the field — not clipped off the bottom, not off to one edge |
| ROI grayscale uniformity | `rf_echo`, `vi_echo`, `int_cv_within` | Even brightness top-to-bottom (TGC ramped smoothly, no bands), **no saturated white blowouts**, deep structures still visible (attenuation compensated) |
| Complete in-frame cross-section | `rf_area`, `vi_area` | Muscle bellies fully inside the frame — not clipped left/right/bottom |
| Subcutaneous fat layer | `muscle_fat_ratio` | Fat layer present and **not flattened** — a sign of light probe pressure |
| Fascicular striations | (quality proxy; see below) | Oblique echogenic striations visible inside the belly — signals correct plane + perpendicular insonation + focus |

Cross-cutting cues that are not structure-specific: the frame is **sharp** (fascia lines thin, not smeared by motion), insonation is **perpendicular** (fascia bright via specular reflection, not dark from anisotropy), and it is a **single clean pane** — no burned-in calipers, measurement text, or body markers over the RF/VI/femur ROI.

Note: the existing `fascicular_contrast` feature is essentially a latent quality signal — it measures how visible the fascicular pattern is, which is a proxy for plane correctness + focus + insonation angle. A partial QA score may already be computed without being named as one.

## 2. What makes a frame susceptible to poor quality

Strictly the "can I extract measurements" lens — not muscle health. Each failure mode maps to which measurements it kills:

| Failure mode | Visual signature | What it breaks |
|---|---|---|
| **Oblique / rotated scan plane** | Fascia lines converge or fade; fascicles out-of-plane | Foreshortens thickness; distorts area; kills pennation/fascicle work |
| **Anisotropy dropout** | One aponeurosis darkens/disappears where the beam tilts | Caliper placement ambiguous → thickness, `full_thickness_q50`, QMT |
| **Probe compression** | Subcutaneous fat flattened, vessels collapsed | Underestimates all thicknesses + areas; biases `muscle_fat_ratio` |
| **Saturation / blown gain** | Flat-white regions, no histogram headroom | Corrupts `rf_echo`, `vi_echo`, `int_cv_within` |
| **TGC banding** | Horizontal bright/dark bands | Depth-dependent echo-intensity bias |
| **Femur out of frame / weak cortex** | No bright cortical line, or no shadow, or off-center | `qmt_mm` and therefore `star_ratio` |
| **Edge clipping** | Muscle mask touches frame border | Truncates `rf_area`, `vi_area` |
| **Motion blur** | Smeared fascia/fascicle lines | Border detection, fascicular contrast, anything needing sharp edges |
| **Air gap / poor coupling** | Reverberation bands, dark wedge at top | Signal loss anywhere in the column |
| **Deep / high-attenuation patient** | Deep aponeurosis and femur faint, low contrast | QMT, deep-structure measurements |
| **Wrong site / wrong muscle** | Anatomy does not match RF mid-thigh | Everything — silently |

The insidious category is the **localized** failures — anisotropy dropout, a reverberation patch — because the frame looks globally fine and only fails *exactly where the caliper lands*. That is why QA has to be ROI-local, not a whole-frame score.

## 3. Algorithms available

Roughly seven classes, weakest-to-strongest for this problem:

**A. Generic no-reference IQA** (BRISQUE, NIQE, PIQE, learned NR-IQA). Trained on natural-image distortions — JPEG, Gaussian blur, noise. Honest verdict: **poor transfer to ultrasound.** Will catch gross blur; blind to oblique plane, anisotropy, what saturation *means*, wrong site. Do not anchor on these.

**B. Deterministic signal/statistical metrics.** Cheap, interpretable, no training: saturation fraction (% near 255), dark-clip fraction, dynamic range, histogram entropy, focus measures (variance-of-Laplacian, Tenengrad, Brenner), speckle SNR in a homogeneous patch, row-wise brightness profile for TGC banding, and the edge-clip check — which is **already computed** as `touches_edge` ([`scripts/preflight_helpers/post_processing.py:106`](../scripts/preflight_helpers/post_processing.py#L106), set inside `post_process_rf`).

**C. Ultrasound-specific, physics-aware.** Ultrasound **confidence maps** (random-walk attenuation model, Karamalis-style) — per-pixel signal confidence that directly flags shadowing, dropout, air gap, anisotropy. The most ultrasound-native tool available, and it can be evaluated *inside the ROI*. Also: reverberation detectors, acoustic-shadow detectors (shadow is *wanted* under the femur but *not* over the muscle).

**D. Structural / task-specific checks.** Highest value, because they test the literal prerequisite for measurement: aponeurosis detectability (Radon/Hough — are there two roughly parallel bright lines bounding the belly, and what fraction of each is continuous?), femur cortex + shadow presence in the central zone. A big chunk of this **already exists**, latent — the QMT validity flags (`femur_absent`, `fascia_slope_high`, `low_muscle_occupancy`, etc., [`labeling_app/measurements.py:51`](../labeling_app/measurements.py#L51)) *are* structural QA, just currently scoped to one measurement.

**E. Learned graders.** A small CNN classifier (pass/borderline/fail) or a quality regressor predicting an expert 1–5 score. The closest published analogs are echo view-quality scoring and obstetric standard-plane detection (SonoNet-style work) — named as domain pointers, not audited citations. Needs labels: realistically a few hundred to low-thousands of frames, and the failure taxonomy must be defined *first* or the labels are noise.

**F. Anomaly / OOD detection.** Autoencoder reconstruction error trained only on good frames, or an OOD score in the segmentation model's feature space. Catches "weird" frames without needing bad-frame labels — a good complement when bad examples are diverse and rare.

**G. Measurement-coupled QA.** The most robust *definition*: a frame is good-for-assessment iff the extractor produces a stable, in-distribution result. Use the segmentation model's own uncertainty (entropy, MC-dropout, ensemble disagreement), mask plausibility checks, and multi-frame consistency — and `frame_rf_consistency` is **already computed** post-hoc for sensitivity ([`labeling_app/measurements.py:1168`](../labeling_app/measurements.py#L1168)); it just is not wired as a gate.

## 4. What is good, practical, and robust — for this pipeline

The most important point: **a QA system already exists — it is just unlabeled-as-such, partial, and entirely human-driven.** Phase-4's three human gates (Usable? / Segmentation perfect? / Use for analysis? — [`labeling_app/assessment_pages/p04_frame_review.py:106`](../labeling_app/assessment_pages/p04_frame_review.py#L106)), plus `touches_edge`/`is_empty`, plus the QMT validity flags, plus `frame_rf_consistency` — that is a QA system with no name and no automation layer. The job is to systematize and partially automate it, not greenfield it.

A build order, cheapest-and-highest-leverage first:

**Step 0 — Write the QA contract (design, ~half a day).** A table: each of the 11 measurements × the structures/conditions it needs × the failure modes that kill it. Output is a spec for a per-frame `image_quality_validity_flags` tuple, modeled exactly on `qmt_validity_flags`. Decide the reason codes now, on paper.

**Step 1 — Capture labels in the labeling app already in operation (do this immediately).** Operators are *already* in phase-4 making pass/fail calls. Turn the free-text "drop reason" into a structured checkbox taxonomy (oblique-plane, fascia-dropout, saturated, compressed, edge-clipped, motion-blur, wrong-site, femur-absent, …). Zero new infrastructure, and within one labeling round this yields a labeled dataset *and* the empirically-real failure distribution. This is the single highest-leverage action and it costs almost nothing.

**Step 2 — Ship a deterministic Tier-0 gate (days).** Class B plus the cheap parts of D: saturation%, dark-clip%, dynamic range, variance-of-Laplacian, TGC-band check, edge-clip (reuse `touches_edge`), and "two fascia lines + femur line present" — readable straight off the existing segmentation masks rather than re-detecting. Emit reason-coded flags into the `FrameFeatures` dataclass. Interpretable, no training, and it can run on the **entire existing cohort immediately** — the cohort-wide failure histogram tells you how big the problem actually is before further investment.

**Step 3 — Add ROI-local confidence-map checks (Tier-1).** Class C, evaluated *in the aponeurosis band and the femur band specifically*. This catches the localized-dropout case Step 2 misses.

**Step 4 — Promote what already exists.** Flip `frame_rf_consistency` from post-hoc sensitivity into a gate. Generalize the QMT-validity-flag pattern to the other measurements. Pull mask plausibility/uncertainty out of the segmentation model — it is free Class-G signal.

**Step 5 — Train the learned grader (Tier-2) — only after Step 1 has accumulated labels.** Validate it against the Tier-0 gate: they must agree on gross cases; the CNN earns its keep only on the borderline band. Do **not** start here — without Step 0+1 you would be hard-coding a guessed taxonomy.

**Step 6 — Wire into the provenance gate.** The integration point is clean: add `image_quality_validity_flags` to the `FrameFeatures` dataclass, write an `image_quality_tier()` modeled on `qmt_tier()` ([`scripts/preflight_helpers/aggregation.py:58`](../scripts/preflight_helpers/aggregation.py#L58)), and the existing non-pooling, tier-preferring aggregation does the rest. The human "Usable?" checkbox stays as the top-level intake filter; the algorithmic flags pre-fill and inform it.

## 5. Strategic things to name before digging in

1. **The name `rf_quality` is a trap.** That package is a measurement/geometry library — `features.geometry`, `features.intensity` — it does *not* do quality assessment despite the name. Whatever gets built for real frame-QA must not live there or share the name, or future-you spends an afternoon confused.

2. **Acquisition QA ≠ assessment-gating QA.** The question asked is assessment-gating ("can I extract measurements from this stored frame"). Fair — but the bad frames were already collected and the operator already left. The bigger long-term win is pushing the same checks to *scan time* so bad frames are never saved. Same algorithms, different consumer, different latency budget. Worth a deliberate decision, not a default.

3. **Resist the single scalar quality score.** The measurements fail independently (Section 1's table is the proof). Per-measurement reason-coded flags, not one number — which is exactly the philosophy already committed to when QMT was refused pooling across tiers.

4. **Operator-picked frames bias the QA labels.** Operators pick ~9 good-looking frames from the full sweep, so the phase-4-labeled set is pre-filtered toward "good." A grader trained only on that data will not generalize to the unpicked frames — which matters the moment acquisition-time QA over the whole sweep is wanted. Cheap to fix now (label some unpicked frames too), expensive to discover after training.

5. **Echo-intensity QA must run on the synthesized grayscale, not the raw jpg.** The `_final` modality is `jpg → convert("L") → resize` ([`scripts/preflight_helpers/segmenter.py:129`](../scripts/preflight_helpers/segmenter.py#L129)). JPEG compression artifacts and the resize are baked in *before* `compute_intensity` ever sees the array. Any saturation/EI quality check has to run post-synthesis on the same array the measurement uses, or it audits the wrong image.

## Next concrete step

The most useful next step is **drafting the Step-0 QA contract table** — the 11-measurements × structures × failure-modes matrix — since everything else keys off it.

---

# Türkçe

## Çerçeve: bir kare soyut olarak değil, *bir ölçüm anonaca* iyidir

"Geçer kare" karenin kendi özelliği değildir — karenin **bir ölçüme göre** özelliğidir. Değerlendirme aşaması yaklaşık 11 şey çıkarır (`compute_all_metrics`, [`labeling_app/measurements.py:1190`](../labeling_app/measurements.py#L1190)): `rf_thickness_mm`, `vi_thickness_mm`, `qmt_mm`, `rf_area_mm2`, `vi_area_mm2`, `rf_echo`, `vi_echo`, `muscle_fat_ratio`, `star_ratio`, `full_thickness_q50_mm`, `int_cv_within`. Bunlar *farklı* görüntü yapılarına dayanır, bu yüzden *bağımsız* olarak başarısız olurlar. Bir kare `rf_echo` anonaca kusursuz, `qmt_mm` anonaca ise işe yaramaz olabilir. Tüm öneri bu gerçeğe dayanır.

## 1. Geçer bir RF karesinin anonabo ipuçları

Her ölçümün dayandığı yapıya göre düzenlenmiştir:

| Yapı | Buna ihtiyaç duyan ölçümler | "İyi" nasıl görünür |
|---|---|---|
| Yüzeyel + derin RF fasyası (aponevrozlar) | `rf_thickness`, `rf_area`, `full_thickness_q50`, `qmt` | İki net, sürekli, neredeyse anonadu ekojenik çizgi, **görüntünün tüm genişliği** boyunca uzanır — sönükleşen segmentler yok, anizotropik sinyal kaybı yok |
| RF/VI arayüzü (derin aponevroz) | `vi_thickness`, `vi_area`, RF–VI ayrımı | Kendi parlak arayüzü olarak ayırt edilebilir, tek bir kas kütlesine bulanıklaşmamış |
| Femur korteksi + akustik gölge | `qmt_mm`, `star_ratio` | Parlak, sürekli, neredeyse anonadu kortikal çizgi ve **altında temiz koyu gölge**, alanın merkez bölgesinde — alttan kırpılmamış, kenara kaymamış |
| ROI gri tonlama tekdüzeliği | `rf_echo`, `vi_echo`, `int_cv_within` | Yukarıdan aşağıya eşit parlaklık (TGC yumuşak rampalı, bantlaşma yok), **satüre olmuş beyaz patlamalar yok**, derin yapılar hâlâ görünür (atenüasyon telafi edilmiş) |
| Kare içinde tam kesit | `rf_area`, `vi_area` | Kas gövdeleri tamamen kare içinde — sağdan/soldan/alttan kırpılmamış |
| Cilt altı yağ tabakası | `muscle_fat_ratio` | Yağ tabakası mevcut ve **ezilmemiş** — hafif prob basısının işareti |
| Fasiküler çizgilenmeler | (kalite vekili; aşağıya bakın) | Gövde içinde oblik ekojenik çizgilenmeler görünür — doğru düzlem + dik insonasyon + odak işareti |

Yapıya özgü olmayan, kesişen ipuçları: kare **anonacf** (fasya çizgileri ince, hareketle yayılmamış), insonasyon **dik** (fasya speküler yansımayla parlak, anizotropiden dolayı koyu değil) ve **tek temiz panel** — RF/VI/femur ROI'sinin üzerinde gömülü kaliper, ölçüm yazısı veya vücut işareti yok.

Not: mevcut `fascicular_contrast` özelliği esasen örtük bir kalite sinyalidir — fasiküler desenin ne kadar görünür olduğunu ölçer; bu da düzlem doğruluğu + odak + insonasyon açısının vekilidir. Kısmi bir QA puanı, böyle adlandırılmadan zaten hesaplanıyor olabilir.

## 2. Bir kareyi düşük kaliteye duyarlı kılan nedir

Kesinlikle "ölçümlerimi çıkarabilir miyim" merceğinden — kas sağlığı değil. Her başarısızlık modu hangi ölçümleri öldürdüğüyle eşleşir:

| Başarısızlık modu | Anonabo imza | Neyi bozar |
|---|---|---|
| **Oblik / dönmüş tarama düzlemi** | Fasya çizgileri yakınsar veya solar; fasiküller düzlem dışı | Kalınlığı kısaltır; alanı bozar; pennasyon/fasikül işini öldürür |
| **Anizotropi sinyal kaybı** | Anonabg eğildiğinde bir aponevroz kararır/kaybolur | Kaliper yerleşimi belirsiz → kalınlık, `full_thickness_q50`, QMT |
| **Prob basısı (kompresyon)** | Cilt altı yağ ezilmiş, damarlar kollabe | Tüm kalınlık + alanları olduğundan az gösterir; `muscle_fat_ratio`'yu yanlılaştırır |
| **Satürasyon / aşırı kazanç** | Düz beyaz bölgeler, histogramda boşluk yok | `rf_echo`, `vi_echo`, `int_cv_within`'i bozar |
| **TGC bantlaşması** | Anonadu parlak/koyu bantlar | Derinliğe bağlı eko yoğunluğu yanlılığı |
| **Femur kare dışı / zayıf korteks** | Parlak kortikal çizgi yok, ya da gölge yok, ya da merkez dışı | `qmt_mm` ve dolayısıyla `star_ratio` |
| **Kenardan kırpılma** | Kas maskesi kare sınırına değiyor | `rf_area`, `vi_area`'yı kesip kısaltır |
| **Hareket bulanıklığı** | Yayılmış fasya/fasikül çizgileri | Sınır tespiti, fasiküler kontrast, anonacf kenar gerektiren her şey |
| **Hava boşluğu / kötü kuplaj** | Reverberasyon bantları, üstte koyu kama | Kolonun herhangi bir yerinde sinyal kaybı |
| **Derin / yüksek atenüasyonlu hasta** | Derin aponevroz ve femur sönük, düşük kontrast | QMT, derin yapı ölçümleri |
| **Yanlış bölge / yanlış kas** | Anatomi RF orta-uyluk ile uyuşmuyor | Her şey — sessizce |

Sinsi kategori **lokalize** başarısızlıklardır — anizotropi sinyal kaybı, bir reverberasyon yaması — çünkü kare genel olarak iyi görünür ve yalnızca *tam kaliperin oturduğu yerde* başarısız olur. QA'nın bütün-kare puanı değil, ROI-yerel olması gerekmesinin sebebi budur.

## 3. Mevcut algoritmalar

Bu problem anonaca zayıftan güçlüye kabaca yedi sınıf:

**A. Genel referanssız IQA** (BRISQUE, NIQE, PIQE, öğrenmeli NR-IQA). Doğal görüntü bozulmalarıyla eğitilmiş — JPEG, Gauss bulanıklığı, gürültü. Dürüst hüküm: **ultrasona zayıf transfer.** Anonacc bulanıklığı yakalar; oblik düzleme, anizotropiye, satürasyonun *anlamına*, yanlış bölgeye kördür. Bunlara dayanmayın.

**B. Deterministik sinyal/istatistik ölçütleri.** Ucuz, yorumlanabilir, eğitim gerektirmez: satürasyon oranı (255'e yakın %), koyu-kırpılma oranı, dinamik aralık, histogram entropisi, odak ölçütleri (Laplacian varyansı, Tenengrad, Brenner), homojen bir yamada benek SNR'si, TGC bantlaşması anonaca satır bazlı parlaklık profili ve kenardan-kırpılma kontrolü — ki bu **zaten hesaplanıyor**, `touches_edge` ([`scripts/preflight_helpers/post_processing.py:106`](../scripts/preflight_helpers/post_processing.py#L106), `post_process_rf` içinde ayarlanır).

**C. Ultrasona özgü, fizik-farkında.** Ultrason **anonabt haritaları** (rastgele-yürüyüş atenüasyon modeli, Karamalis tarzı) — gölgelenmeyi, sinyal kaybını, hava boşluğunu, anizotropiyi doğrudan işaretleyen piksel başına sinyal güveni. Mevcut en ultrason-doğal araç ve *ROI içinde* değerlendirilebilir. Ayrıca: reverberasyon dedektörleri, akustik gölge dedektörleri (gölge femur altında *istenir* ama kas üzerinde *istenmez*).

**D. Yapısal / göreve özgü kontroller.** En yüksek değer, çünkü ölçümün gerçek ön koşulunu test ederler: aponevroz tespit edilebilirliği (Radon/Hough — gövdeyi sınırlayan kabaca paralel iki parlak çizgi var mı ve her birinin ne kadarı sürekli?), merkez bölgede femur korteksi + gölge varlığı. Bunun büyük bir kısmı **zaten var**, örtük olarak — QMT geçerlilik bayrakları (`femur_absent`, `fascia_slope_high`, `low_muscle_occupancy`, vb., [`labeling_app/measurements.py:51`](../labeling_app/measurements.py#L51)) *yapısal QA'dır*, sadece şu an tek bir ölçümle sınırlı.

**E. Öğrenmeli değerlendiriciler.** Anonach bir CNN sınıflandırıcı (geçer/sınırda/kalır) veya uzman 1–5 puanını tahmin eden bir kalite regresörü. En yakın yayımlanmış benzerleri eko görüntü-kalitesi puanlaması ve obstetrik standart-düzlem tespiti (SonoNet tarzı çalışmalar) — denetlenmiş atıf değil, alan işaretçisi olarak adlandırıldı. Etiket gerektirir: gerçekçi olarak birkaç yüz ila birkaç bin kare ve başarısızlık taksonomisi *önce* tanımlanmalı, yoksa etiketler gürültüdür.

**F. Anomali / OOD (dağılım-dışı) tespiti.** Yalnızca iyi karelerle eğitilmiş otokodlayıcı yeniden-yapılandırma hatası, ya da segmentasyon modelinin öznitelik uzayında bir OOD puanı. Kötü-kare etiketi gerektirmeden "tuhaf" kareleri yakalar — kötü örnekler çeşitli ve nadir olduğunda iyi bir tamamlayıcı.

**G. Ölçüme-bağlı QA.** En sağlam *tanım*: bir kare değerlendirme-anonaca-iyidir ancak ve ancak çıkarıcı kararlı, dağılım-içi bir sonuç üretiyorsa. Segmentasyon modelinin kendi belirsizliğini (entropi, MC-dropout, topluluk uyuşmazlığı), maske akla yatkınlık kontrollerini ve çok-kareli tutarlılığı kullanın — `frame_rf_consistency` duyarlılık anonaca **zaten hesaplanıyor** ([`labeling_app/measurements.py:1168`](../labeling_app/measurements.py#L1168)); sadece bir kapı olarak bağlanmamış.

## 4. Bu pipeline anonaca iyi, anonada ve sağlam olan

En önemli nokta: **bir QA sistemi zaten var — sadece öyle etiketlenmemiş, kısmi ve tamamen insan-güdümlü.** Faz-4'ün üç insan kapısı (Kullanılabilir mi? / Segmentasyon kusursuz mu? / Analize dahil et? — [`labeling_app/assessment_pages/p04_frame_review.py:106`](../labeling_app/assessment_pages/p04_frame_review.py#L106)), artı `touches_edge`/`is_empty`, artı QMT geçerlilik bayrakları, artı `frame_rf_consistency` — bu, adı olmayan ve otomasyon katmanı olmayan bir QA sistemidir. İş, onu sistematikleştirmek ve kısmen otomatikleştirmek, sıfırdan kurmak değil.

En ucuz-ve-en yüksek kaldıraçtan başlayarak bir inşa sırası:

**Adım 0 — QA sözleşmesini yazın (tasarım, ~yarım gün).** Bir tablo: 11 ölçümün her biri × ihtiyaç duyduğu yapılar/koşullar × onu öldüren başarısızlık modları. Çıktı, tam olarak `qmt_validity_flags` örnek alınarak modellenmiş, kare başına bir `image_quality_validity_flags` demeti anonaca bir spesifikasyon. Sebep kodlarına şimdi, kağıt üzerinde karar verin.

**Adım 1 — Zaten kullanımda olan etiketleme uygulamasında etiket toplayın (bunu hemen yapın).** Operatörler faz-4'te *zaten* geçer/kalır kararları veriyor. Serbest anonacj "drop reason" alanını yapısal bir onay kutusu taksonomisine dönüştürün (oblik-düzlem, fasya-kaybı, satüre, kompresyonlu, kenardan-kırpık, hareket-bulanık, yanlış-bölge, femur-yok, …). Sıfır yeni altyapı ve bir etiketleme turu içinde bu, etiketli bir veri kümesi *ve* ampirik-gerçek başarısızlık dağılımı verir. Bu, tek başına en yüksek kaldıraçlı eylemdir ve neredeyse hiçbir maliyeti yoktur.

**Adım 2 — Deterministik bir Kademe-0 kapısı yayınlayın (günler).** Sınıf B artı D'nin ucuz kısımları: satürasyon%, koyu-kırpılma%, dinamik aralık, Laplacian varyansı, TGC-bant kontrolü, kenardan-kırpılma (`touches_edge`'i yeniden kullanın) ve "iki fasya çizgisi + femur çizgisi mevcut" — yeniden tespit etmek yerine doğrudan mevcut segmentasyon maskelerinden okunabilir. Sebep-kodlu bayrakları `FrameFeatures` dataclass'ına yazın. Yorumlanabilir, eğitim gerektirmez ve **mevcut tüm kohort üzerinde hemen** çalıştırılabilir — kohort genelindeki başarısızlık histogramı, daha fazla yatırım yapmadan önce sorunun gerçekte ne kadar büyük olduğunu söyler.

**Adım 3 — ROI-yerel anonabt haritası kontrolleri ekleyin (Kademe-1).** Sınıf C, *özellikle aponevroz bandında ve femur bandında* değerlendirilir. Bu, Adım 2'nin kaçırdığı lokalize-kayıp durumunu yakalar.

**Adım 4 — Zaten var olanı terfi ettirin.** `frame_rf_consistency`'yi post-hoc duyarlılıktan bir kapıya çevirin. QMT-geçerlilik-bayrağı desenini diğer ölçümlere genelleyin. Maske akla yatkınlığını/belirsizliğini segmentasyon modelinden çekin — bu bedava Sınıf-G sinyalidir.

**Adım 5 — Öğrenmeli değerlendiriciyi (Kademe-2) eğitin — yalnızca Adım 1 etiket biriktirdikten sonra.** Onu Kademe-0 kapısına karşı doğrulayın: anonacc durumlarda uyuşmalılar; CNN değerini yalnızca sınırdaki bantta kazanır. Buradan **başlamayın** — Adım 0+1 olmadan tahmini bir taksonomiyi sabit-kodlamış olursunuz.

**Adım 6 — Köken (provenance) kapısına bağlayın.** Entegrasyon noktası temiz: `FrameFeatures` dataclass'ına `image_quality_validity_flags` ekleyin, `qmt_tier()` örnek alınarak modellenmiş bir `image_quality_tier()` yazın ([`scripts/preflight_helpers/aggregation.py:58`](../scripts/preflight_helpers/aggregation.py#L58)) ve mevcut havuzlamayan, kademe-tercih eden agregasyon gerisini halleder. İnsan "Kullanılabilir mi?" onay kutusu en üst düzey giriş filtresi olarak kalır; algoritmik bayraklar onu önceden doldurur ve bilgilendirir.

## 5. Kazmaya başlamadan önce adı konması gereken stratejik konular

1. **`rf_quality` adı bir tuzaktır.** O paket bir ölçüm/geometri kütüphanesidir — `features.geometry`, `features.intensity` — adına rağmen kalite değerlendirmesi *yapmaz*. Gerçek kare-QA anonaca inşa edilen şey orada yaşamamalı veya adı paylaşmamalı, yoksa gelecekteki anonadi bir öğleden sonranı kafan karışmış halde geçirirsin.

2. **Edinim QA'sı ≠ değerlendirme-kapısı QA'sı.** Sorulan soru değerlendirme-kapısıdır ("bu saklanmış kareden ölçüm çıkarabilir miyim"). Adil — ama kötü kareler zaten toplanmış ve operatör çoktan gitmiş. Daha büyük anonadr vadeli kazanç, aynı kontrolleri *tarama anına* itmek, böylece kötü kareler hiç kaydedilmesin. Aynı algoritmalar, farklı tüketici, farklı gecikme bütçesi. Varsayılan değil, bilinçli bir karar gerektirir.

3. **Tek skaler kalite puanına direnin.** Ölçümler bağımsız başarısız olur (Bölüm 1'in tablosu kanıttır). Tek bir sayı değil, ölçüm başına sebep-kodlu bayraklar — ki bu, QMT kademeler arası havuzlamadan reddedilirken zaten taahhüt edilen felsefedir.

4. **Operatör-seçimli kareler QA etiketlerini yanlılaştırır.** Operatörler tüm taramadan ~9 iyi görünen kare seçer, yani faz-4-etiketli küme "iyi"ye doğru önceden filtrelenmiştir. Yalnızca bu veriyle eğitilen bir değerlendirici, seçilmemiş karelere genellenmez — ki bu, tüm tarama üzerinde tarama-anı QA'sı istendiği anda önemli olur. Şimdi düzeltmesi ucuz (bazı seçilmemiş kareleri de etiketleyin), eğitimden sonra keşfetmesi pahalı.

5. **Eko-yoğunluğu QA'sı, ham jpg üzerinde değil sentezlenmiş gri tonlama üzerinde çalışmalı.** `_final` modalitesi `jpg → convert("L") → resize`'dır ([`scripts/preflight_helpers/segmenter.py:129`](../scripts/preflight_helpers/segmenter.py#L129)). JPEG sıkıştırma artefaktları ve yeniden boyutlandırma, `compute_intensity` diziyi görmeden *önce* pişmiştir. Herhangi bir satürasyon/EI kalite kontrolü, ölçümün kullandığı aynı dizi üzerinde, sentez sonrası çalışmalı; aksi halde yanlış görüntüyü denetler.

## Sıradaki somut adım

En yararlı sıradaki adım, **Adım-0 QA sözleşme tablosunu taslaklamaktır** — 11 ölçüm × yapılar × başarısızlık modları matrisi — çünkü diğer her şey ona dayanır.
