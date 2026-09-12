import bot


def test_price_and_area_detection():
    text = "مبل راحتی برای فروش در کادیکوی، قیمت ۲۵۰۰۰ لیر"
    assert bot.has_price(text)
    assert bot.find_area(text) == "کادیکوی"
    assert bot.message_is_ad(text)


def test_price_with_persian_thousands_is_detected():
    assert bot.has_price("قیمت ۱۰ هزار لیر")


def test_turkish_neighborhood_and_lira_are_detected():
    text = "Sony Bravia 55 XF90 - 4K HDR Android Smart TV Fiat: 30000 TL Erenkoy-19 Mayıs"
    assert bot.has_price(text)
    assert bot.find_area(text) in {"erenkoy", "19 mayis", "19 mayıs"}


def test_kagithane_address_is_detected():
    text = "فروش کلیه لوازم منزل، محل: İstanbul / Kağıthane، یخچال ۱۳۰۰۰ لیر"
    assert bot.has_price(text)
    assert bot.find_area(text) in {"kağıthane", "kagithane"}


def test_all_district_style_names_and_neighborhood_suffix_are_detected():
    assert bot.find_area("محل: Ümraniye") == "Ümraniye"
    assert bot.find_area("Adres: Merkez Mahallesi") == "merkez"
    assert bot.find_area("İstanbul / Yenibosna") == "yenibosna"


def test_comma_separated_gokturk_address_is_detected():
    text = "فروش وسایل منزل، آدرس: İstanbul, Göktürk"
    assert not bot.has_price(text)
    assert bot.find_area(text) in {"göktürk", "gokturk"}


def test_missing_price_is_reported():
    text = "لباس زنانه نو برای فروش در شیشلی، سایز متوسط"
    assert not bot.has_price(text)
    assert bot.find_area(text) == "شیشلی"


def test_non_ad_is_ignored():
    assert not bot.message_is_ad("سلام دوستان")


def test_photo_without_caption_is_inspected():
    assert bot.should_inspect_message("", has_photo=True)
