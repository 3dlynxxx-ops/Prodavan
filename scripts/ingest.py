#!/usr/bin/env python3
"""Збирає сирі CSV агентів однієї ніші в одну таблицю й готує документи для дошки.

Використання:
  python3 scripts/ingest.py <prefix> <seg> <id_letter> <out_csv> <seed_dir> [wave]
  напр.: python3 scripts/ingest.py games S4 G leads/games-leads.csv /tmp/seed4 4

- читає leads/raw/<prefix>-*.csv (формат брифу агентів: segment,priority,company,city,type,...)
- прибирає дублікати між файлами і з уже наявними лідами (leads/raw/_existing-all.txt)
- пише <out_csv> і JSON-документи для бази дошки (<seed_dir>/<ID>.json)
- перше повідомлення генерується за тоном docs/03-tone-of-voice.md
"""
import csv, glob, json, os, re, sys, collections

INTRO = "Вітаю!\n\nМене звуть Антон, я керівник 3D-майстерні 3D Lynx з Ужгорода."
SIG = "\n\nАнтон, 3D Lynx\n+380 50 036 03 33 · t.me/dddlynxxx"
CTA = "\n\nЧи могли б ми коротко зідзвонитися або зустрітися, щоб розповісти більше? Як вам зручніше."
CTA_LOCAL = "\n\nМи теж з Закарпаття, тож я можу заїхати й показати вироби наживо. Коли вам було б зручно?"
PHOTO = "\n\nНадсилаю фото кількох робіт як приклад."

BODY = {
    # книжки
    'книгарня': " Ми виготовляємо кастомні 3D-вироби, зокрема букнуки, а зараз особливою популярністю в нас користуються закладинки для книжок. Хочемо запропонувати їх і вам.\n\nНадсилаю фото кількох закладок як приклад.",
    'видавництво': " Ми робимо кастомні 3D-вироби для книжкового світу: закладинки, букнуки, фігурки персонажів. Подумали, що для ваших видань це може бути цікаво: наприклад, закладинка чи фігурка улюбленого персонажа до новинки." + PHOTO,
    'бібліотека/музей': " Серед наших робіт є лінійка «Постаті», фігурки видатних українців, а також 3D-закладинки для книжок. Подумали, що це могло б гарно доповнити вашу крамницю." + PHOTO,
    # настолки
    'видавець настолок': " Ми робимо кастомні 3D-вироби для настільних ігор: деталізовані мініатюри, органайзери для коробок, апгрейд-компоненти. Подумали, що для ваших ігор це могло б бути цікаво: наприклад, мініатюри героїв чи фігурка-символ для презентації новинки." + PHOTO,
    'автор/інді-проєкт': " Ми допомагаємо авторам настільних ігор утілювати ідеї у фізичні компоненти: мініатюри, фішки, органайзери, прототипи. Подумали, що для вашого проєкту це може бути цікаво, наприклад якщо думаєте про делюкс-версію чи мініатюри для кампанії." + PHOTO,
    'магазин настолок': " Ми робимо аксесуари для настільних ігор: органайзери, вежі для кубиків, апгрейд-компоненти, а також деталізовані мініатюри. Думаємо, вони добре доповнили б ваш асортимент." + PHOTO,
    'клуб/антикафе': " Ми робимо авторські 3D-трофеї, фігурки й аксесуари для ігор. Подумали, що для вашого клубу могли б бути цікавими власні кубки для турнірів чи фігурка-символ клубу." + PHOTO,
    'фестиваль/турнір': " Ми робимо авторські 3D-трофеї, фігурки й мерч. Подумали, що для ваших турнірів чи фестивалю могли б бути цікавими власні кубки або фігурка-символ події." + PHOTO,
    'рольові/варгейм': " Ми друкуємо мініатюри на смоляному принтері з високою деталізацією, а також тереин і декорації для ігрових столів. Подумали, що для вашої спільноти це може бути цікаво." + PHOTO,
    'квест-кімната': " Ми робимо кастомні 3D-вироби: реквізит, декор, деталі механізмів. Подумали, що для ваших квестів це могло б бути цікаво, наприклад унікальний реквізит під сюжет кімнати." + PHOTO,
    'медіа/блогер': " Ми робимо фігурки й мерч. Подумали, що для вашого каналу міг би бути цікавим власний мерч, наприклад фігурка-маскот для глядачів." + PHOTO,
}
DEFAULT = {'S1': 'книгарня', 'S4': 'магазин настолок'}
LOCAL = re.compile(r'Ужгород|Закарпат|Мукачев|Берегов|Хуст|Виноградів|Свалява|Перечин', re.I)
F = ['priority', 'company', 'city', 'type', 'website', 'instagram', 'what_they_do', 'why_fit', 'offer_angle',
     'decision_maker_role', 'contact_name', 'email', 'phone', 'telegram', 'linkedin', 'contact_form',
     'source_url', 'notes', 'activity']
ORD = {'A': 0, 'B': 1, 'C': 2}


def norm(s):
    s = re.sub(r'\(.*?\)|«|»|"', '', s.lower())
    s = re.split(r'\s+[—–/|-]\s+|,', s)[0]
    return re.sub(r'[^a-zа-яіїєґ0-9]', '', s)


def message(seg, typ, local):
    body = BODY.get(typ) or BODY[DEFAULT.get(seg, 'магазин настолок')]
    return INTRO + body + (CTA_LOCAL if local else CTA) + SIG


def main(prefix, seg, letter, out_csv, seed_dir, wave='4'):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    existing = {norm(x.split(' | ')[0]) for x in open(os.path.join(root, 'leads/raw/_existing-all.txt'), encoding='utf-8').read().splitlines() if x.strip()}
    groups = collections.OrderedDict()
    for f in sorted(glob.glob(os.path.join(root, f'leads/raw/{prefix}-*.csv'))):
        for r in csv.DictReader(open(f, encoding='utf-8')):
            k = norm(r['company'])
            if not k or k in existing:
                continue
            groups.setdefault(k + '|' + norm(r.get('city', ''))[:6], []).append(r)
    rows = []
    for v in groups.values():
        v.sort(key=lambda r: ORD.get(r.get('priority'), 9))
        m = dict(v[0])
        for r in v[1:]:
            for f in F:
                if not (m.get(f) or '').strip():
                    m[f] = r.get(f, '')
        rows.append(m)
    rows.sort(key=lambda r: (ORD.get(r.get('priority'), 9), r.get('type', ''), r.get('city', '')))
    os.makedirs(seed_dir, exist_ok=True)
    out = []
    for i, m in enumerate(rows, 1):
        d = {f: (m.get(f) or '') for f in F}
        local = bool(LOCAL.search(d['city']))
        d.update(seg=seg, segment=m.get('segment') or seg, niche=d['type'] or 'інше', wave=int(wave), tiers=[],
                 local=local, msg=message(seg, d['type'], local), status='новий', next_step='', next_date='', touched='')
        doc_id = f'{letter}{i:03d}'
        json.dump(d, open(os.path.join(seed_dir, doc_id + '.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        out.append({'id': doc_id, **d})
    with open(os.path.join(root, out_csv), 'w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['id', 'niche'] + F + ['status'], quoting=csv.QUOTE_ALL, extrasaction='ignore')
        w.writeheader()
        for x in out:
            w.writerow(x)
    with open(os.path.join(root, 'leads/raw/_existing-all.txt'), 'a', encoding='utf-8') as fh:
        for x in out:
            fh.write(f"\n{x['company']} | {x['city']}")
    print(len(out), dict(collections.Counter(x['priority'] for x in out)), dict(collections.Counter(x['niche'] for x in out)))


if __name__ == '__main__':
    main(*sys.argv[1:])
