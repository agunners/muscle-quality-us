# RF Görüntü Alma — Tek Sayfa Operatör Kontrol Listesi (v8)

*Gerçek zamanlı hızlı bakış. Ayrıntı/gerekçe: Görüntü Alma Kılavuzu v8. Round-3 = tek cihaz, tek probe (Leltek 128L). **Ana kohort: yalnız DOMİNANT bacak.** Her iki bacak yalnızca alt-kohortta (bkz. F).*

## A. ÇEKİME BAŞLAMADAN — GERİ DÖNÜŞÜ YOK
- ☐ **RAW CINE: ON** (Engineering Mode; kalıcı).
- ☐ **Probe marker HER ZAMAN HASTANIN SAĞINA** (iki bacakta da aynı). → Sol/sağ ayrımı buna bağlı.
- ☐ **Fasyalar + cilt-altı yağ + fasya düzlemleri NET.** Değilse → **TEKRAR ÇEK.** *(Kasın yağlı görünmesi sinyaldir, kusur değil — TEKNİK netlik aranır.)*
- ☐ Sweep'te **proximal-distal kayma YOK** — yalnız lateral→medial.
- ☐ Her karede **RF ortada, EN BÜYÜK kesit.**

## B. AYARLAR (her hastada aynı)
**Probe 128L · Preset MSK (önce MSK!) · Gain 35 · Enhancement 8 · Derinlik 6.9 / 4.9 cm · Freq 8.3 / 10 MHz**

*(Gain 35, kılavuz v8 ve canlı Drive kontrol listesiyle aynı — bu kopya 2026-09-02'ye kadar yanlışlıkla 40 yazıyordu.)*

## C. İŞARETLEME (çekimden önce — en kritik adım)
- ☐ **Dominant bacağı** belirle ve datasheet'e yaz (ana kohort bu bacaktan çekilir).
- ☐ **ASIS – patella üst kenarı** ölç → cm datasheet'e. RF'in ortalandığı / en geniş olduğu yeri görerek **%50'yi dermografla işaretle.**
- ☐ Prob bölgesini/hattını işaretle — görüntüler ve sweep buradan geçecek. *(Alt-kohortta iki bacağı da işaretle.)*

## D. PATIENT INFO (cihazda — datasheet'i bu besler)
**MRN** = tarih_sıra · **First** = ad · **Middle** = yaş_cinsiyet_boy_kilo (örn. 50e_176_86) · **Last** = soyad · **Performed by** = anonacz. Aynı hastada her seferinde AYNI gir.

## E. ÇEKİM SIRASI — DOMİNANT BACAK (ana kohort)
Her kayıt: **iyi görüntü → FREEZE → MAKE VIDEO → SAVE IMAGE → çık.**

| # | Kayıt | Derinlik | Freq | Not |
|:-:|:--|:--:|:--:|:--|
| 1 | **FEMUR** | 6.9 cm → gerekirse derinleştir | 8.3 MHz | Femur korteksi + RF üst fasya net (yalnız kalınlık anonaca) — femur görünmüyorsa derinliği gerektiği kadar artır |
| 2 | **RF-1** | **4.9 cm — SABİT** | 10 MHz | Tam merkez (%50). FEMUR'dan sonra derinliği 4.9'a GERİ AL; RF-1 hiçbir zaman başka derinlikte çekilmez |
| 3 | **Sweep** (lateral→medial) | 4.9 cm → alt fasya anonaca gerekirse derinleştir | 10 MHz | Yavaş, düz, 5–8 sn; proximal-distal kayma yok. RF'in ALT fasyası sweep boyunca kadrajda değilse YALNIZ derinliği artır (RF-1'den SONRA) ve Notlar'a yaz |

→ **Toplam 3** mp4 + 3 RawImages + 3 .dcm (tek bacak).

## F. ALT-KOHORT (yalnız seçili 25 hasta — HER İKİ BACAK)
Sağ ve sol anonaca ayrı ayrı: **FEMUR → RF-1 → (probu kaldır+geri koy) RF-2 → Sweep** = 4 kayıt/bacak.
→ **Toplam 8** mp4 + 8 RawImages + 8 .dcm. Ayrıca: **aynı gün iki operatör + 24 anonadc sonra tekrar** (1–7 gün kabul). Datasheet'te **'Alt-kohort mu' = Evet.**

## G. KALİTE — KABUL / TEKRAR ÇEK
**Kabul:** fasya + yağ-fasya net · prob dik (tilt yok) · bası minimal · sweep lateral→medial tam · proximal-distal kayma yok · FEMUR korteksi net · RF ortada ve en büyük · ayarlar protokole uygun.
**Tekrar çek:** fasya/yağ seçilemiyor · hasta kasıyor · aşırı bası · sweep hızlı/kaydı · merkez/RF kaydı.

## H. REVIEW → EXPORT
- ☐ **End Exam YAPMADAN** → REVIEW → yanlış mp4/RawImages **sil** (eksikse tamamla).
- ☐ Sayıyı doğrula: **ana = 3, alt-kohort = 8.** Yalnız protokole uygun görüntüleri **zip** → Drive `goruntuler/<operatör>/`.
- ☐ Datasheet: ASIS-Patella + Dominant Bacak + öykü; **'Dosyalar Tam mı' işaretle.**

*Hasta: supin, quadriceps gevşek, ayak parmakları yukarı, ≥5 dk dinlenmiş, çekimden ≥24 s önce ağır egzersiz yok.*
