# Tableau Public dashboard rehberi

`uv run tbl analyze` her çalıştığında `~/data/tbl/tableau/tbl-2026-27/` klasörünü yeniler:
CSV dosyaları ve `court.png` (FIBA yarı saha, 15 m × 14 m, dip çizgi altta).

> **Herkese açık:** Tableau Public'e kaydedilen her workbook ve içindeki veri herkesin
> görebileceği yerdedir. Kaydederken "Workbook ve verisinin indirilmesine izin ver"
> seçeneğini kapatın; yine de veriyi gizli sayamayız.

## 0. Kurulum
1. [public.tableau.com](https://public.tableau.com) üzerinden ücretsiz hesap açın ve
   **Tableau Public** (Desktop Public Edition) uygulamasını indirin.
2. **Connect → To a File → Text file** ile `shots.csv` dosyasını açın. Her CSV ayrı bir veri
   kaynağıdır; birleştirme (join) gerekmez, isimler dosyaların içinde.
3. Diğer dosyaları **Data → New Data Source** ile ekleyin.

## Dosyalar

| Dosya | Satır | Ne için |
|---|---|---|
| `shots.csv` | atış | şut haritası, xPTS, bölge, bağlam filtreleri |
| `player_shooting.csv` | oyuncu | şut kalitesi ve beklenenin üstünde atma |
| `player_zone_shooting.csv` | oyuncu × bölge | bölge profili, lig ortalamasıyla karşılaştırma |
| `team_shooting.csv` | takım × hücum/savunma | takımın aldığı ve verdiği şut kalitesi |
| `pairs.csv` | takım × iki oyuncu | birlikte / ayrı net reyting |
| `assist_pairs.csv` | pas veren → atan | asist ağı |
| `score_timeline.csv` | her sayı | maç akışı grafiği |
| `game_flow.csv`, `runs.csv` | takım-maç / seri | geri dönüşler, seriler |
| `clutch_team.csv`, `clutch_player.csv` | takım / oyuncu | son 5 dakika, fark ≤ 5 |

Alan sözlüğü:
- `xfg`: atışın isabet olasılığı. `xpts` = `xfg` × 2 veya 3.
- `poe`: sayı − xPTS, yani beklenenin üstünde (+) ya da altında (−) atılan sayı.
- `poe_se`: `poe_per_shot` değerinin gürültüsü. |poe_per_shot| değeri 2 × `poe_se`'den
  küçükse fark şans olabilir.
- `xpts_per_shot`: **şut kalitesi**, yani takımın ya da oyuncunun nasıl şutlar aldığı.
- `plot_x`, `plot_y`: metre cinsinden; plot_x yan çizgiden yan çizgiye (0–15), plot_y
  dip çizgiden (0–14).
- `transition`: savunma ribaundu, top çalma ya da top kaybından sonra ilk 7 saniyede
  gelen atış.

Bir takımın (Keçiören) kısa kodu kaynakta boş; grafiklerde `team` alanını kullanın.

## 1. Şut haritası (shots.csv)
1. **Analysis → Aggregate Measures** seçeneğini kapatın. Her nokta bir atış olur.
2. `plot_x` alanını Columns'a, `plot_y` alanını Rows'a sürükleyin.
3. Eksenleri sabitleyin: eksene sağ tıklayın → **Edit Axis → Fixed**. X için 0–15,
   Y için 0–14 girin. Ardından **Format → Lines** ile ızgara çizgilerini kapatın.
4. **Map → Background Images → shots.csv → Add Image** ile `court.png` dosyasını seçin.
   X Field `plot_x` (Left 0, Right 15), Y Field `plot_y` (Bottom 0, Top 14).
5. Marks türünü **Circle** yapın; `made` alanını Color'a koyun.
6. Filtre olarak `team`, `player`, `zone`, `period`, `clutch`, `transition` alanlarını
   ekleyin ve **Show Filter** ile görünür yapın.

**Altıgen ısı haritası (isteğe bağlı):** Aggregate Measures'ı tekrar açın ve şu
hesaplanmış alanları oluşturun (altıgen boyu 0,6 m):

```
Hex X = HEXBINX([plot_x] / 0.6, [plot_y] / 0.6) * 0.6
Hex Y = HEXBINY([plot_x] / 0.6, [plot_y] / 0.6) * 0.6
POE / şut = (SUM([pts]) - SUM([xpts])) / COUNT([pts])
Şut sayısı = COUNT([pts])
```

`Hex X`'i Columns'a, `Hex Y`'yi Rows'a koyun (sürekli, boyut olarak). `POE / şut`'u
Color'a koyun; renk paleti iki yönlü ve ortası 0 olsun. `Şut sayısı`'nı Size'a
koyun. 5'ten az atışı olan altıgenleri bir filtreyle gizleyin.

## 2. Oyuncu: şut kalitesi × şut başarısı (player_shooting.csv)
- Columns: `xpts_per_shot`. Rows: `poe_per_shot`. Detail: `player`. Color: `team`.
  Size: `shots`.
- `shots` alanıyla bir filtre ekleyin, en az 60.
- Analytics bölmesinden her iki eksene ortalama referans çizgisi ekleyin.
- Sağ üst köşedekiler iyi şut alıp üstüne bir de iyi atıyor. Sol üsttekiler zor şut
  alıyor ama iyi atıyor.
- Tooltip'e `poe_se` alanını ekleyin; oyuncunun farkı gürültüden büyük mü, görülsün.

## 3. Takım: hücum ve savunma şut kalitesi (team_shooting.csv)
- Rows: `team`. Columns: `xpts_per_shot`. Color: `side`. Hücum ve savunma yan yana
  çubuklar olsun.
- Savunmada düşük `xpts_per_shot` rakibe kötü şut aldırmak demek. Savunmada negatif
  `poe_per_shot` ise rakibin şutları kaçırması demek; bunun bir kısmı şanstır.

## 4. İkililer (pairs.csv, assist_pairs.csv)
- **Isı haritası:** Rows `player_a`, Columns `player_b`, Color `AVG(net)` (iki yönlü
  renk). `team` filtresini tek değer yapın. `poss` filtresi en az 100 olsun.
  Tooltip'e `minutes`, `poss`, `a_without_b_net`, `b_without_a_net` ekleyin.
- **Birlikte vs ayrı:** Bir ikili seçin; `net`, `a_without_b_net`, `b_without_a_net`
  değerlerini Measure Names / Measure Values ile çubuk olarak gösterin.
- **Asist ağı:** assist_pairs.csv'de Rows `passer`, Columns `scorer`, Size ve Label
  `assists`. Kare (Square) mark kullanın.

## 5. Maç akışı (score_timeline.csv, game_flow.csv, runs.csv)
Hesaplanmış alanlar:

```
Maç = STR([date]) + "  " + [home] + " - " + [away]
Önde = IF [margin_home] > 0 THEN [home] ELSEIF [margin_home] < 0 THEN [away] ELSE "Eşit" END
```

- Columns `minute` (sürekli), Rows `margin_home`. Mark: Line.
- **Path → Line Type → Step** seçin; skor basamak basamak değişsin.
- `Maç` alanını tek değerli filtre yapın; 10, 20, 30 ve 40. dakikalara referans
  çizgisi ekleyin.
- game_flow.csv'den bir tablo ekleyin: `biggest_lead`, `biggest_run`,
  `lead_changes_game`, `times_tied`, `largest_deficit`. Kazanan takımda
  `largest_deficit` en büyük geri dönüştür.
- runs.csv: `points` ≥ 10 olan serileri listeleyin.

## 6. Kritik anlar (clutch_team.csv, clutch_player.csv)
- Rows `team`, Columns `net`. Label olarak `poss` ve `clutch_games` gösterin.
- Takım başına 10–25 hücum gibi çok az veri var. Grafik başlığında bunu belirtin.

## 7. Dashboard ve yayın
1. **Dashboard → New Dashboard**. Sayfaları sürükleyin; filtreleri **Apply to Worksheets →
   All Using This Data Source** ile ortak yapın.
2. **File → Save to Tableau Public As…**. Hesabınızla giriş yapın.
3. Yayından sonra workbook sayfasında **Edit Details** bölümünden indirme iznini kapatın.
4. Veri değişince `uv run tbl analyze` çalıştırın, Tableau'da **Data → Refresh All
   Extracts** yapın ve yeniden kaydedin.
