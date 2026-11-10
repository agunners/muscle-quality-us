Q:  
give me the simple mm-ing pipeline   
get this column multiply with this scale with this, kind of..  
I will carry this logic to other folders etc.  
be practical

A:

Use this exact pipeline.

**Columns you need**

- `pixel_spacing_x`  
- `pixel_spacing_y`  
- `dcm_rows`  
- `jpg_height`  
- `jpg_width`  
- `mask_height`  
- `mask_width`

**Generic formula**

\# 1\) Recover ultrasound width in DICOM-pixel units

dcm\_cols\_usg \= jpg\_width \* dcm\_rows / jpg\_height

\# 2\) Physical spacing at MASK resolution

mm\_per\_mask\_y \= pixel\_spacing\_y \* dcm\_rows / mask\_height

mm\_per\_mask\_x \= pixel\_spacing\_x \* dcm\_cols\_usg / mask\_width

\# 3\) If you measure a MODEL OUTPUT mask, map mask-res spacing to output-res spacing

scale\_s \= min(resize\_to\_h / mask\_height, resize\_to\_w / mask\_width)

crop\_h \= resize\_to\_h \* valid\_scale

crop\_w \= resize\_to\_w \* valid\_scale

mm\_per\_out\_y \= (mm\_per\_mask\_y / scale\_s) \* (crop\_h / out\_h)

mm\_per\_out\_x \= (mm\_per\_mask\_x / scale\_s) \* (crop\_w / out\_w)

\# 4\) Convert measurements

thickness\_mm \= thickness\_px \* mm\_per\_out\_y

area\_mm2 \= area\_px \* mm\_per\_out\_x \* mm\_per\_out\_y

**For your fold-2 model**

- `resize_to = (512, 256)`  
- `valid_scale = 1.0`  
- output mask is typically `384 x 192`

If the source mask is already `512 x 256`, this simplifies to:

dcm\_cols\_usg \= jpg\_width \* dcm\_rows / jpg\_height

mm\_per\_pred\_y \= pixel\_spacing\_y \* dcm\_rows / 384

mm\_per\_pred\_x \= pixel\_spacing\_x \* dcm\_cols\_usg / 192

max\_thickness\_mm \= max\_thickness\_px \* mm\_per\_pred\_y

p95\_thickness\_mm \= p95\_thickness\_px \* mm\_per\_pred\_y

csa\_mm2 \= area\_px \* mm\_per\_pred\_x \* mm\_per\_pred\_y

**Practical rule**

- Thickness uses only vertical spacing: `mm_per_*_y`  
- Area uses both axes: `mm_per_*_x * mm_per_*_y`  
- If any of the spacing columns are blank, return `n/a`  
- Do not use raw `dcm_cols` here; this notebook reconstructs ultrasound width with `jpg_width * dcm_rows / jpg_height`

**Real example**

- `pixel_spacing_x = pixel_spacing_y = 0.13486072`  
- `dcm_rows = 585`  
- `jpg = 512 x 249`  
- prediction mask \= `384 x 192`

Then:

- `dcm_cols_usg = 284.50`  
- `mm_per_pred_y = 0.20545`  
- `mm_per_pred_x = 0.19983`

So:

- `50 px` thickness \-\> `10.27 mm`  
- `10000 px` area \-\> about `410.6 mm²`

This is the same logic implemented in [0317\_review\_baseline.ipynb:817](http:///home/akadir/dev/muscle_model_training/notebooks/0317_review_baseline.ipynb#L817) with model geometry from [fold2 JSON:23](http:///home/akadir/dev/muscle_model_training/transport/models/0319_voi_hist_clahe_45seed_native_512x256_v5___300_samples____fold2_DeepLabV3Plus_timm-efficientnet-b1.json#L23).

Q:  
I am trying to understand this line:  
scale\_s \= min(resize\_to\_h / mask\_height, resize\_to\_w / mask\_width)  
isn't this a fading scale, with a min?

A:  
It is a `min`, but it is not a “fading” scale. It is the uniform fit scale.

Meaning:

scale\_s \= min(resize\_to\_h / mask\_height, resize\_to\_w / mask\_width)

asks:

- “How much can I scale the image in height before hitting the target height?”  
- “How much can I scale the image in width before hitting the target width?”  
- Then take the smaller one so both dimensions still fit.

This is the standard aspect-ratio-preserving resize-with-padding rule.

Example:

- original mask: `559 x 370`  
- target canvas: `512 x 256`

Then:

- `512 / 559 = 0.916`  
- `256 / 370 = 0.692`

So:

scale\_s \= min(0.916, 0.692) \= 0.692

Apply that single scale to both axes:

- new height: `559 * 0.692 = 387`  
- new width: `370 * 0.692 = 256`

Now it fits inside `512 x 256`, and the remaining height is padding.

Why this matters for mm conversion:

- before resize, one mask pixel represents `mm_per_mask_*`  
- after shrinking by `scale_s`, there are fewer pixels for the same anatomy  
- so each resized pixel represents more millimeters

That is why the notebook does:

mm\_per\_resized \= mm\_per\_mask / scale\_s

So yes, `min(...)` is intentional. It is the “fit inside target without distortion” scale, not an arbitrary weakening factor.

If you want a better name mentally, read it as:

fit\_scale

not “fade scale”.

This is the assumption used in [0317\_review\_baseline.ipynb:817](http:///home/akadir/dev/muscle_model_training/notebooks/0317_review_baseline.ipynb#L817).

Q:  
so I need to do it? It is not given?

A:  
Yes, if your measurement is on a resized model-space mask. No, if your measurement is on the original annotation mask.

Practical rule:

- Original mask space: `scale_s` is not needed.  
- Resized/padded model input space: `scale_s` is needed.  
- Final model output space: `scale_s` is needed, and you also need the last resize factor to output size.

So in your setup:

- `resize_to` is given in [fold2 model JSON](http:///home/akadir/dev/muscle_model_training/transport/models/0319_voi_hist_clahe_45seed_native_512x256_v5___300_samples____fold2_DeepLabV3Plus_timm-efficientnet-b1.json#L39)  
- `mask_height` / `mask_width` come from each CSV row in [the split CSV](http:///home/akadir/dev/muscle_model_training/data/metadata/splits/combined_metadata_all_base__stratified_group_kfold__seed45__0319_voi_hist_clahe_45seed_native_512x256_v5___300_samples____fold2_deeplabv3plus_timm-efficientnet-b1.csv#L1)  
- `scale_s` itself is not stored anywhere; it is computed in [the review notebook](http:///home/akadir/dev/muscle_model_training/notebooks/0317_review_baseline.ipynb#L817)

Use this mental model:

fit\_scale \= min(target\_h / orig\_h, target\_w / orig\_w)

That is just “how much did I shrink/enlarge the original mask to fit the target canvas without distortion?”

Use these cases:

\# 1\) If you measure on original mask

thickness\_mm \= thickness\_px \* mm\_per\_mask\_y

area\_mm2 \= area\_px \* mm\_per\_mask\_x \* mm\_per\_mask\_y

\# 2\) If you measure on resized 512x256 canvas

thickness\_mm \= thickness\_px \* (mm\_per\_mask\_y / fit\_scale)

area\_mm2 \= area\_px \* (mm\_per\_mask\_x / fit\_scale) \* (mm\_per\_mask\_y / fit\_scale)

\# 3\) If you measure on final 384x192 prediction

thickness\_mm \= thickness\_px \* (mm\_per\_mask\_y / fit\_scale) \* (crop\_h / out\_h)

area\_mm2 \= area\_px \* (mm\_per\_mask\_x / fit\_scale) \* (crop\_w / out\_w) \* (mm\_per\_mask\_y / fit\_scale) \* (crop\_h / out\_h)

If your new folders already contain masks in the exact resolution you will measure, skip `scale_s`.   
