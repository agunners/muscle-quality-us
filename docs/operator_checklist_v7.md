# RF Görüntü Alma — Tek Sayfa Operatör Kontrol Listesi (v7)

*Gerçek zamanlı hızlı bakış. Ayrıntı ve gerekçe: Görüntü Alma Kılavuzu v7. Round-3 = tek cihaz, tek probe (Leltek 128L).*

## A. ÇEKİME BAŞLAMADAN — GERİ DÖNÜŞÜ YOK
- ☐ **RAW CINE: ON** (Engineering Mode; kalıcı).
- ☐ **Probe marker HER ZAMAN HASTANIN SAĞINA** (iki bacakta da aynı). → Sol/sağ bacak ayrımı buna bağlı.
- ☐ **Fasyalar + cilt-altı yağ + fasya düzlemleri NET.** Değilse → **TEKRAR ÇEK.** *(Kasın yağlı görünmesi sorun değil — o bizim sinyalimiz; burada TEKNİK netlik aranır.)*
- ☐ Sweep'te **proximal-distal kayma YOK** — yalnız lateral→medial.
- ☐ Her karede **RF ortada, EN BÜYÜK kesit.**

## B. AYARLAR (her hastada aynı)
**Probe 128L · Preset MSK (önce MSK!) · Gain 40 · Enhancement 8 · Derinlik 6.9 / 4.9 cm · Freq 10 / 8.3 MHz**

## C. İŞARETLEME (çekimden önce, iki bacak — en kritik adım)
- ☐ **ASIS – patella üst kenarı** ölç → cm datasheet'e (sol + sağ ayrı).
- ☐ Çizginin **%50'sini dermografla işaretle.** RF'in ortalandığı / en geniş olduğu yeri görerek işaretle.
- ☐ Prob bölgesini/hattını işaretle — görüntüler ve sweep buradan geçecek.

## D. HER BACAK — ÇEKİM SIRASI
Her kayıt: **iyi görüntü → FREEZE → MAKE VIDEO → SAVE IMAGE → çık.**

| # | Kayıt | Derinlik | Freq | Not |
|:-:|:--|:--:|:--:|:--|
| 1 | **FEMUR** | 6.9 cm | 8.3 MHz | Femur korteksi + RF üst fasya net (yalnız kalınlık ölçümü anonaca; gerekiyorsa ayar oynanır) |
| 2 | **RF-1** | 4.9 cm | 10 MHz | Tam merkez (%50) |
| 3 | **Sweep** (lateral→medial) | 6.9 cm | 10 MHz | Yavaş, düz, 5–8 sn; proximal-distal kayma yok |

**Alt-kohort (yalnız seçili 25 hasta):** RF-1'den sonra **probu kaldır + geri koy → RF-2** ekle (toplam 4 kayıt/bacak).

## E. KALİTE — KABUL / TEKRAR ÇEK
**Kabul:** fasya + yağ-fasya net · prob dik (tilt yok) · bası minimal · sweep lateral→medial tam, ani yön yok · proximal-distal kayma yok · FEMUR korteksi net · RF ortada ve en büyük · ayarlar protokole uygun.
**Tekrar çek:** fasya/yağ seçilemiyor · hasta kasıyor · aşırı bası · sweep hızlı veya kaydı · merkez/RF kaydı.

## F. KAYIT SAYISI (zipten önce doğrula)
- **Ana protokol:** her bacak **3** → **toplam 6** mp4 + 6 RawImages klasörü.
- **Alt-kohort:** her bacak **4** → **toplam 8.**

## G. REVIEW → EXPORT
- ☐ **End Exam YAPMADAN** → REVIEW → yanlış mp4/RawImages **sil** (eksikse tamamla, ana klasöre eklenir).
- ☐ Yalnız protokole uygun görüntüleri **zip** → Drive `goruntuler/<operatör>/`.
- ☐ Datasheet: Boy / Kilo + öykü; **'Dosyalar Tam mı' / 'Export Tam' işaretle.**

*Hasta: supin, quadriceps gevşek, ayak parmakları yukarı, ≥5 dk dinlenmiş, çekimden ≥24 s önce ağır egzersiz yok. Dominant bacak kaydedilir.*
