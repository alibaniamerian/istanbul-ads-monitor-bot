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


def test_report_recipients_include_second_manager():
    assert 112484108 in bot.ADMIN_CHAT_IDS
    assert len(bot.ADMIN_CHAT_IDS) == len(set(bot.ADMIN_CHAT_IDS))


def test_duplicate_lookback_is_48_hours():
    assert bot.DUPLICATE_LOOKBACK_HOURS == 48


def test_group_admin_statuses_are_excluded():
    assert bot.is_group_admin_status("administrator")
    assert bot.is_group_admin_status("creator")
    assert not bot.is_group_admin_status("member")


def test_correction_message_lists_missing_fields():
    message = bot.build_correction_message("فروش مبل در کادیکوی")
    assert message is not None
    assert "قیمت" in message
    assert "همین آگهی را طوری ویرایش کنید" in message
    assert "آدرس: کادیکوی" in message
    assert "قیمت: ۲۰۰۰۰ لیر" in message


def test_missing_price_is_reported():
    text = "لباس زنانه نو برای فروش در شیشلی، سایز متوسط"
    assert not bot.has_price(text)
    assert bot.find_area(text) == "شیشلی"


def test_area_name_is_detected_anywhere_but_not_inside_another_word():
    assert bot.find_area("میز مدل کادیکوی، رنگ سفید") == "کادیکوی"
    assert bot.find_area("محصول کادیکویلی، رنگ سفید") is None


def test_non_ad_is_ignored():
    assert not bot.message_is_ad("سلام دوستان")


def test_photo_without_caption_is_inspected():
    assert bot.should_inspect_message("", has_photo=True)
