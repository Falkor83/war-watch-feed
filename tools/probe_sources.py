"""
War Watch: try every candidate news source and data endpoint from GitHub's servers and report which ones work.
Run by hand (Actions -> Probe sources). Writes probe.json (uploaded as an artifact) and prints a summary.
Standard library only.
"""
import json, re, sys, time, datetime, urllib.request, urllib.parse, concurrent.futures as cf
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (compatible; WarWatchBot/0.3; +https://github.com/Falkor83/war-watch-feed)"

# name | home ISO-2 | language | url
RSS = """
BBC Arabic|GB|ar|https://feeds.bbci.co.uk/arabic/rss.xml
BBC Persian|GB|fa|https://feeds.bbci.co.uk/persian/rss.xml
BBC Russian|GB|ru|https://feeds.bbci.co.uk/russian/rss.xml
BBC Ukrainian|GB|uk|https://feeds.bbci.co.uk/ukrainian/rss.xml
BBC Mundo|GB|es|https://feeds.bbci.co.uk/mundo/rss.xml
BBC Brasil|GB|pt|https://feeds.bbci.co.uk/portuguese/rss.xml
BBC Afrique|GB|fr|https://feeds.bbci.co.uk/afrique/rss.xml
BBC Hausa|GB|ha|https://feeds.bbci.co.uk/hausa/rss.xml
BBC Swahili|GB|sw|https://feeds.bbci.co.uk/swahili/rss.xml
BBC Somali|GB|so|https://feeds.bbci.co.uk/somali/rss.xml
BBC Amharic|GB|am|https://feeds.bbci.co.uk/amharic/rss.xml
BBC Tigrinya|GB|ti|https://feeds.bbci.co.uk/tigrinya/rss.xml
BBC Burmese|GB|my|https://feeds.bbci.co.uk/burmese/rss.xml
BBC Urdu|GB|ur|https://feeds.bbci.co.uk/urdu/rss.xml
BBC Hindi|GB|hi|https://feeds.bbci.co.uk/hindi/rss.xml
BBC Bengali|GB|bn|https://feeds.bbci.co.uk/bengali/rss.xml
BBC Turkce|GB|tr|https://feeds.bbci.co.uk/turkce/rss.xml
BBC Chinese|GB|zh|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
BBC Vietnamese|GB|vi|https://feeds.bbci.co.uk/vietnamese/rss.xml
BBC Indonesia|GB|id|https://feeds.bbci.co.uk/indonesia/rss.xml
BBC Korean|GB|ko|https://feeds.bbci.co.uk/korean/rss.xml
BBC Japanese|GB|ja|https://feeds.bbci.co.uk/japanese/rss.xml
BBC Pashto|GB|ps|https://feeds.bbci.co.uk/pashto/rss.xml
BBC Afaan Oromoo|GB|om|https://feeds.bbci.co.uk/afaanoromoo/rss.xml
BBC Gahuza|GB|rw|https://feeds.bbci.co.uk/gahuza/rss.xml
BBC Uzbek|GB|uz|https://feeds.bbci.co.uk/uzbek/rss.xml
BBC Azeri|GB|az|https://feeds.bbci.co.uk/azeri/rss.xml
BBC Nepali|GB|ne|https://feeds.bbci.co.uk/nepali/rss.xml
DW Arabic|DE|ar|https://rss.dw.com/rdf/rss-ar-all
DW Russian|DE|ru|https://rss.dw.com/rdf/rss-ru-all
DW Ukrainian|DE|uk|https://rss.dw.com/rdf/rss-uk-all
DW Persian|DE|fa|https://rss.dw.com/rdf/rss-fa-all
DW Spanish|DE|es|https://rss.dw.com/rdf/rss-sp-all
DW Turkish|DE|tr|https://rss.dw.com/rdf/rss-tur-all
DW Chinese|DE|zh|https://rss.dw.com/rdf/rss-chi-all
DW German|DE|de|https://rss.dw.com/rdf/rss-de-all
DW Swahili|DE|sw|https://rss.dw.com/rdf/rss-kis-all
DW Hausa|DE|ha|https://rss.dw.com/rdf/rss-hau-all
DW Amharic|DE|am|https://rss.dw.com/rdf/rss-amh-all
DW Dari|DE|fa|https://rss.dw.com/rdf/rss-dar-all
DW Urdu|DE|ur|https://rss.dw.com/rdf/rss-urd-all
France 24 French|FR|fr|https://www.france24.com/fr/rss
France 24 Arabic|FR|ar|https://www.france24.com/ar/rss
France 24 Spanish|FR|es|https://www.france24.com/es/rss
RFI Monde|FR|fr|https://www.rfi.fr/fr/monde/rss
RFI Afrique|FR|fr|https://www.rfi.fr/fr/afrique/rss
RFI Spanish|FR|es|https://www.rfi.fr/es/rss
RFI Persian|FR|fa|https://www.rfi.fr/fa/rss
Le Monde International|FR|fr|https://www.lemonde.fr/international/rss_full.xml
Le Figaro International|FR|fr|https://www.lefigaro.fr/rss/figaro_international.xml
Jeune Afrique|FR|fr|https://www.jeuneafrique.com/feed/
Spiegel Ausland|DE|de|https://www.spiegel.de/ausland/index.rss
Tagesschau Ausland|DE|de|https://www.tagesschau.de/ausland/index~rss2.xml
FAZ Ausland|DE|de|https://www.faz.net/rss/aktuell/politik/ausland/
NZZ International|CH|de|https://www.nzz.ch/international.rss
Der Standard International|AT|de|https://www.derstandard.at/rss/international
El Pais Internacional|ES|es|https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/internacional/portada
El Mundo Internacional|ES|es|https://e00-elmundo.uecdn.es/elmundo/rss/internacional.xml
Infobae|AR|es|https://www.infobae.com/arc/outboundfeeds/rss/
Clarin Mundo|AR|es|https://www.clarin.com/rss/mundo/
La Jornada Mundo|MX|es|https://www.jornada.com.mx/rss/mundo.xml
El Universal Mundo|MX|es|https://www.eluniversal.com.mx/rss/mundo.xml
Proceso|MX|es|https://www.proceso.com.mx/rss/
El Tiempo Mundo|CO|es|https://www.eltiempo.com/rss/mundo.xml
El Espectador|CO|es|https://www.elespectador.com/arc/outboundfeeds/rss/?outputType=xml
El Universo|EC|es|https://www.eluniverso.com/arc/outboundfeeds/rss/?outputType=xml
Efecto Cocuyo|VE|es|https://efectococuyo.com/feed/
14ymedio|CU|es|https://www.14ymedio.com/rss/
Cubadebate|CU|es|http://www.cubadebate.cu/feed/
Granma English|CU|en|https://en.granma.cu/feed
Folha Mundo|BR|pt|https://feeds.folha.uol.com.br/mundo/rss091.xml
G1 Mundo|BR|pt|https://g1.globo.com/rss/g1/mundo/
Agencia Brasil|BR|pt|https://agenciabrasil.ebc.com.br/rss/internacional/feed.xml
Publico Mundo|PT|pt|https://feeds.feedburner.com/PublicoMundo
Corriere Esteri|IT|it|https://xml2.corriereobjects.it/rss/esteri.xml
ANSA Mondo|IT|it|https://www.ansa.it/sito/notizie/mondo/mondo_rss.xml
Repubblica Esteri|IT|it|https://www.repubblica.it/rss/esteri/rss2.0.xml
TVN24 Swiat|PL|pl|https://tvn24.pl/swiat.xml
PAP|PL|pl|https://www.pap.pl/rss.xml
NOS Buitenland|NL|nl|https://feeds.nos.nl/nosnieuwsbuitenland
Ukrainska Pravda UA|UA|uk|https://www.pravda.com.ua/rss/view_news/
Ukrinform English|UA|en|https://www.ukrinform.net/rss/block-lastnews
Ukrinform Ukrainian|UA|uk|https://www.ukrinform.ua/rss/block-lastnews
TASS English|RU|en|https://tass.com/rss/v2.xml
TASS|RU|ru|https://tass.ru/rss/v2.xml
RIA Novosti|RU|ru|https://ria.ru/export/rss2/archive/index.xml
Kommersant|RU|ru|https://www.kommersant.ru/RSS/news.xml
Interfax|RU|ru|https://www.interfax.ru/rss.asp
RBC|RU|ru|https://rssexport.rbc.ru/rbcnews/news/30/full.rss
Meduza Russian|RU|ru|https://meduza.io/rss/all
Novaya Gazeta Europe|RU|ru|https://novayagazeta.eu/feed/rss
Hurriyet Dunya|TR|tr|https://www.hurriyet.com.tr/rss/dunya
Anadolu Turkish|TR|tr|https://www.aa.com.tr/tr/rss/default?cat=dunya
TRT Haber Dunya|TR|tr|https://www.trthaber.com/dunya_articles.rss
Daily Sabah|TR|en|https://www.dailysabah.com/rssFeed/world
Al Arabiya English|SA|en|https://english.alarabiya.net/feed/rss2/en.xml
Al Arabiya|SA|ar|https://www.alarabiya.net/feed/rss2/ar.xml
Asharq Al-Awsat English|SA|en|https://english.aawsat.com/feed
Asharq Al-Awsat|SA|ar|https://aawsat.com/feed
Arab News|SA|en|https://www.arabnews.com/rss.xml
Gulf News|AE|en|https://gulfnews.com/feed
Al-Monitor|US|en|https://www.al-monitor.com/rss
Ahram Online|EG|en|https://english.ahram.org.eg/rss/
Sky News Arabia|AE|ar|https://www.skynewsarabia.com/rss
Al Mayadeen English|LB|en|https://english.almayadeen.net/rss
L'Orient Today|LB|en|https://today.lorientlejour.com/rss
Naharnet|LB|en|https://www.naharnet.com/stories/en/rss
Enab Baladi|SY|en|https://english.enabbaladi.net/feed/
SANA English|SY|en|https://sana.sy/en/?feed=rss2
Rudaw|IQ|en|https://www.rudaw.net/rss/english
Shafaq News|IQ|en|https://shafaq.com/en/rss
Kurdistan24|IQ|en|https://www.kurdistan24.net/en/rss
IRNA English|IR|en|https://en.irna.ir/rss
Tehran Times|IR|en|https://www.tehrantimes.com/rss
Press TV|IR|en|https://www.presstv.ir/rss.xml
Mehr English|IR|en|https://en.mehrnews.com/rss
Tasnim English|IR|en|https://www.tasnimnews.com/en/rss/feed/0/7/0/
IranWire|IR|en|https://iranwire.com/en/feed/
Iran International|IR|en|https://www.iranintl.com/en/feed
Jerusalem Post|IL|en|https://www.jpost.com/rss/rssfeedsfrontpage.aspx
Ynet News|IL|en|https://www.ynetnews.com/Integration/StoryRss3089.xml
Ynet Hebrew|IL|he|https://www.ynet.co.il/Integration/StoryRss2.xml
Haaretz|IL|en|https://www.haaretz.com/srv/haaretz-latest-headlines
i24NEWS|IL|en|https://www.i24news.tv/en/rss
WAFA English|PS|en|https://english.wafa.ps/rss
Palestine Chronicle|PS|en|https://www.palestinechronicle.com/feed/
Mondoweiss|US|en|https://mondoweiss.net/feed/
Saba English|YE|en|https://www.saba.ye/en/rss
Xinhua English|CN|en|http://www.xinhuanet.com/english/rss/worldrss.xml
CGTN World|CN|en|https://www.cgtn.com/subscribe/rss/section/world.xml
Global Times|CN|en|https://www.globaltimes.cn/rss/outbrain.xml
China Daily World|CN|en|http://www.chinadaily.com.cn/rss/world_rss.xml
Lianhe Zaobao World|SG|zh|https://www.zaobao.com.sg/rss/realtime/world
RTHK International|HK|en|https://rthk.hk/rthk/news/rss/e_expressnews_einternational.xml
Focus Taiwan|TW|en|https://focustaiwan.tw/rss
Liberty Times|TW|zh|https://news.ltn.com.tw/rss/world.xml
NHK World|JP|en|https://www3.nhk.or.jp/rss/news/cat6.xml
NHK International|JP|ja|https://www3.nhk.or.jp/rss/news/cat6.xml
Kyodo|JP|en|https://english.kyodonews.net/rss/all.xml
Mainichi English|JP|en|https://mainichi.jp/english/rss/etc/english.rss
Korea Herald|KR|en|https://www.koreaherald.com/common/rss_xml.php?ct=104
Hankyoreh English|KR|en|https://english.hani.co.kr/rss/
Yonhap Korean|KR|ko|https://www.yna.co.kr/rss/news.xml
NK News|US|en|https://www.nknews.org/feed/
Daily NK|KR|en|https://www.dailynk.com/english/feed/
Indian Express World|IN|en|https://indianexpress.com/section/world/feed/
Hindustan Times World|IN|en|https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml
NDTV World|IN|en|https://feeds.feedburner.com/ndtvnews-world-news
Express Tribune|PK|en|https://tribune.com.pk/feed/world
Geo News|PK|en|https://www.geo.tv/rss/1/2
TOLOnews|AF|en|https://tolonews.com/rss.xml
Pajhwok|AF|en|https://pajhwok.com/feed/
Daily Star Bangladesh|BD|en|https://www.thedailystar.net/world/rss.xml
Bangkok Post World|TH|en|https://www.bangkokpost.com/rss/data/world.xml
VnExpress International|VN|en|https://e.vnexpress.net/rss/world.rss
Inquirer Global Nation|PH|en|https://globalnation.inquirer.net/feed
Mizzima|MM|en|https://eng.mizzima.com/feed
Myanmar Now|MM|en|https://myanmar-now.org/en/feed/
CNA World|SG|en|https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6311
BenarNews|US|en|https://www.benarnews.org/english/rss
Daily Nation|KE|en|https://nation.africa/kenya/rss.xml
The East African|KE|en|https://www.theeastafrican.co.ke/tea/rss
Garowe Online|SO|en|https://www.garoweonline.com/en/rss
Hiiraan Online|SO|en|https://www.hiiraan.com/rss
Addis Standard|ET|en|https://addisstandard.com/feed/
Sudans Post|SS|en|https://www.sudanspost.com/feed/
Radio Tamazuj|SS|en|https://www.radiotamazuj.org/en/rss
Eye Radio|SS|en|https://www.eyeradio.org/feed/
Radio Okapi|CD|fr|https://www.radiookapi.net/rss.xml
Actualite.cd|CD|fr|https://actualite.cd/rss.xml
The New Times Rwanda|RW|en|https://www.newtimes.co.rw/rss
Punch|NG|en|https://punchng.com/feed/
Vanguard|NG|en|https://www.vanguardngr.com/feed/
Daily Trust|NG|en|https://dailytrust.com/feed/
HumAngle|NG|en|https://humanglemedia.com/feed/
Lefaso.net|BF|fr|https://lefaso.net/spip.php?page=backend
Maliweb|ML|fr|https://www.maliweb.net/feed
ActuNiger|NE|fr|https://www.actuniger.com/feed
Club of Mozambique|MZ|en|https://clubofmozambique.com/feed/
Zitamar|MZ|en|https://www.zitamar.com/feed/
Journal du Cameroun|CM|fr|https://www.journalducameroun.com/feed/
Tchadinfos|TD|fr|https://tchadinfos.com/feed/
Corbeau News|CF|fr|https://corbeaunews-centrafrique.org/feed/
Libya Observer|LY|en|https://libyaobserver.ly/rss.xml
Libya Herald|LY|en|https://libyaherald.com/feed/
News24 World|ZA|en|https://feeds.news24.com/articles/news24/World/rss
Daily Maverick|ZA|en|https://www.dailymaverick.co.za/dmrss/
ISS Africa|ZA|en|https://issafrica.org/rss
Haitian Times|HT|en|https://haitiantimes.com/feed/
Le Nouvelliste|HT|fr|https://lenouvelliste.com/rss
InSight Crime|CO|en|https://insightcrime.org/feed/
CBC World|CA|en|https://www.cbc.ca/webfeed/rss/rss-world
NYT World|US|en|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
Washington Post World|US|en|https://feeds.washingtonpost.com/rss/world
CNN World|US|en|http://rss.cnn.com/rss/edition_world.rss
Fox News World|US|en|https://moxie.foxnews.com/google-publisher/world.xml
CBS News World|US|en|https://www.cbsnews.com/latest/rss/world
ABC News US International|US|en|https://abcnews.go.com/abcnews/internationalheadlines
Defense News|US|en|https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml
Breaking Defense|US|en|https://breakingdefense.com/feed/
The War Zone|US|en|https://www.twz.com/feed
Stars and Stripes|US|en|https://www.stripes.com/arc/outboundfeeds/rss/?outputType=xml
USNI News|US|en|https://news.usni.org/feed
Long War Journal|US|en|https://www.longwarjournal.org/feed
Crisis Group|BE|en|https://www.crisisgroup.org/rss.xml
Politico Europe|BE|en|https://www.politico.eu/feed/
Euractiv|BE|en|https://www.euractiv.com/feed/
LRT English|LT|en|https://www.lrt.lt/en/rss
ERR News|EE|en|https://news.err.ee/rss
LSM English|LV|en|https://eng.lsm.lv/rss/
Balkan Insight|RS|en|https://balkaninsight.com/feed/
Kathimerini English|GR|en|https://www.ekathimerini.com/rss
NewsMaker Moldova|MD|en|https://newsmaker.md/en/feed/
IPN Moldova|MD|en|https://www.ipn.md/en/rss
Civil Georgia|GE|en|https://civil.ge/feed
Novinite|BG|en|https://www.novinite.com/services/news_rdf.php
Yle News|FI|en|https://yle.fi/rss/news
ReliefWeb|UN|en|https://reliefweb.int/updates/rss.xml
UN News Arabic|UN|ar|https://news.un.org/feed/subscribe/ar/news/all/rss.xml
UN News French|UN|fr|https://news.un.org/feed/subscribe/fr/news/all/rss.xml
UN News Spanish|UN|es|https://news.un.org/feed/subscribe/es/news/all/rss.xml
UN News Russian|UN|ru|https://news.un.org/feed/subscribe/ru/news/all/rss.xml
UN News Chinese|UN|zh|https://news.un.org/feed/subscribe/zh/news/all/rss.xml
"""

# Google News "World" page per country edition: hl | gl | ceid
GNEWS = """
en-GB|GB|GB:en
en-IN|IN|IN:en
en-NG|NG|NG:en
en-ZA|ZA|ZA:en
en-KE|KE|KE:en
en-PK|PK|PK:en
en-PH|PH|PH:en
en-AU|AU|AU:en
en-CA|CA|CA:en
en-SG|SG|SG:en
en-IL|IL|IL:en
en-US|US|US:en
fr|FR|FR:fr
de|DE|DE:de
es|ES|ES:es
es-419|MX|MX:es-419
es-419|CO|CO:es-419
pt-BR|BR|BR:pt-419
it|IT|IT:it
pl|PL|PL:pl
uk|UA|UA:uk
ru|RU|RU:ru
tr|TR|TR:tr
ar|EG|EG:ar
ar|SA|SA:ar
ar|LB|LB:ar
ar|AE|AE:ar
he|IL|IL:he
ja|JP|JP:ja
ko|KR|KR:ko
zh-TW|TW|TW:zh-Hant
zh-HK|HK|HK:zh-Hant
id|ID|ID:id
vi|VN|VN:vi
th|TH|TH:th
hi|IN|IN:hi
"""

DATA = {
    "gdelt_artlist_all_langs": "https://api.gdeltproject.org/api/v2/doc/doc?query=(Hormuz%20OR%20IRGC%20OR%20Qeshm)&mode=artlist&format=json&maxrecords=75&timespan=1d&sort=datedesc",
    "gdelt_timelinevol": "https://api.gdeltproject.org/api/v2/doc/doc?query=(Taiwan%20OR%20Taipei)%20(military%20OR%20drills%20OR%20PLA)&mode=timelinevol&format=json&timespan=3m",
    "gdelt_timelinetone": "https://api.gdeltproject.org/api/v2/doc/doc?query=(Taiwan%20OR%20Taipei)%20(military%20OR%20drills%20OR%20PLA)&mode=timelinetone&format=json&timespan=3m",
    "gdelt_timelinelang": "https://api.gdeltproject.org/api/v2/doc/doc?query=(Taiwan%20OR%20Taipei)%20(military%20OR%20drills%20OR%20PLA)&mode=timelinelang&format=json&timespan=1m",
    "gdelt_timelinesourcecountry": "https://api.gdeltproject.org/api/v2/doc/doc?query=(Taiwan%20OR%20Taipei)%20(military%20OR%20drills%20OR%20PLA)&mode=timelinesourcecountry&format=json&timespan=1m",
    "us_travel_advisories_json": "https://cadataapi.state.gov/api/TravelAdvisories",
    "us_travel_advisories_rss": "https://travel.state.gov/_res/rss/TAsTWs.xml",
    "uk_fcdo_index": "https://www.gov.uk/api/content/foreign-travel-advice",
    "uk_fcdo_taiwan": "https://www.gov.uk/api/content/foreign-travel-advice/taiwan",
    "fx_latest": "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
    "fx_dated": "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@2026-10-05/v1/currencies/usd.json",
    "fx_er_api": "https://open.er-api.com/v6/latest/USD",
    "oil_stooq_brent": "https://stooq.com/q/d/l/?s=cb.f&i=d",
    "oil_fred_brent": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU",
    "ioda_outages": "https://api.ioda.inetintel.cc.gatech.edu/v2/outages/alerts?entityType=country&from={from}&until={until}",
    "polymarket_search": "https://gamma-api.polymarket.com/public-search?q=taiwan&limit_per_type=5",
    "reliefweb_api": "https://api.reliefweb.int/v2/reports?appname=warwatch&limit=3",
    "gdacs_rss": "https://www.gdacs.org/xml/rss.xml",
}

def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(6_000_000)
        return r.status, body, round(time.time() - t0, 1), r.geturl()

def local(tag): return tag.rsplit("}", 1)[-1].lower()

def parse_feed(body):
    root = ET.fromstring(body)
    items = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"): continue
        title, date, link = "", "", ""
        for ch in el:
            n = local(ch.tag)
            if n == "title" and not title: title = (ch.text or "").strip()
            elif n in ("pubdate", "published", "updated", "date") and not date: date = (ch.text or "").strip()
            elif n == "link" and not link: link = (ch.text or ch.get("href") or "").strip()
        if title: items.append((title, date, link))
    return items

def probe_feed(name, home, lang, url):
    try:
        st, body, secs, final = fetch(url)
        items = parse_feed(body)
        return {"name": name, "home": home, "lang": lang, "url": url, "ok": len(items) > 0, "status": st, "secs": secs,
                "items": len(items), "final": final if final != url else "", "sample": [i[0][:90] for i in items[:2]],
                "dates": [i[1][:40] for i in items[:1]]}
    except Exception as e:
        return {"name": name, "home": home, "lang": lang, "url": url, "ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}

def main():
    rows = [l.split("|") for l in RSS.strip().splitlines() if l.strip()]
    gn = [l.split("|") for l in GNEWS.strip().splitlines() if l.strip()]
    gtasks = [(f"Google News {gl} ({hl})", gl, hl.split("-")[0],
               f"https://news.google.com/rss/headlines/section/topic/WORLD?hl={hl}&gl={gl}&ceid={ceid}") for hl, gl, ceid in gn]
    report = {"at": datetime.datetime.utcnow().isoformat() + "Z", "rss": [], "gnews": [], "data": {}}
    with cf.ThreadPoolExecutor(24) as ex:
        report["rss"] = list(ex.map(lambda r: probe_feed(*r), rows))
    for t in gtasks:                                       # gently, one at a time
        report["gnews"].append(probe_feed(*t)); time.sleep(1.5)
    now = int(time.time())
    for k, u in DATA.items():
        u = u.replace("{from}", str(now - 3 * 86400)).replace("{until}", str(now))
        try:
            st, body, secs, final = fetch(u, timeout=40)
            txt = body.decode("utf-8", errors="replace")
            info = {"ok": True, "status": st, "secs": secs, "bytes": len(body), "head": txt[:600]}
            if k.startswith("gdelt_artlist"):
                arts = json.loads(txt).get("articles", [])
                langs = {}
                for a in arts: langs[a.get("language")] = langs.get(a.get("language"), 0) + 1
                info.update(articles=len(arts), langs=langs, countries=sorted({a.get("sourcecountry") for a in arts})[:40],
                            sample=[(a.get("language"), a.get("sourcecountry"), a.get("domain"), a.get("title", "")[:100]) for a in arts[:12]])
            report["data"][k] = info
        except Exception as e:
            report["data"][k] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        if k.startswith("gdelt"): time.sleep(6)
    json.dump(report, open("probe.json", "w"), ensure_ascii=False, indent=1)
    good = [r for r in report["rss"] if r["ok"]]
    print(f"[OK] RSS: {len(good)} of {len(report['rss'])} work")
    for r in report["rss"]:
        print(("OK  " if r["ok"] else "BAD ") + f"{r['name']}|{r['home']}|{r['lang']}|{r.get('items', 0)}|{r.get('error', '')}|{(r.get('sample') or [''])[0]}")
    print(f"[OK] Google News: {sum(r['ok'] for r in report['gnews'])} of {len(report['gnews'])} work")
    for r in report["gnews"]:
        print(("OK  " if r["ok"] else "BAD ") + f"{r['name']}|{r.get('items', 0)}|{r.get('error', '')}|{(r.get('sample') or [''])[0]}")
    for k, v in report["data"].items():
        print(f"DATA {k}: " + json.dumps({x: v[x] for x in v if x != "head"}, ensure_ascii=False)[:700])
        if v.get("head"): print("     head: " + v["head"][:300].replace("\n", " "))

if __name__ == "__main__":
    main()
