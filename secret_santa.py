from __future__ import annotations

import argparse
import os
import random
import re
from dataclasses import dataclass
from dataclasses import field
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP
from typing import Any
from typing import Iterable
from typing import Iterator
from typing import Optional
from typing import Protocol
from typing import Sequence
from typing import Union

import dotenv
import html2text
import markdown
import requests
import yaml
from bs4 import BeautifulSoup
from html2image import Html2Image
from jinja2 import Environment
from jinja2 import FileSystemLoader
from jinja2 import Template

dotenv.load_dotenv()


INPUT_FILENAME = "input.yaml"
NOTE_TXT = "Having trouble viewing this email? Try viewing it in a web browser."

SCRAPER_REGEX = re.compile(r"(?P<name>amazon)/(?P<code>[A-Z0-9]{10})")
URL_REGEX = re.compile(r"https?://.*")

FONTS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;700&family=Mountains+of+Christmas:wght@400;700&display=swap" rel="stylesheet">
"""


@dataclass
class Input:
    """Input data for the secret santa"""

    filename: str = field(default=INPUT_FILENAME)
    _input: dict[str, Any] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        with open(self.filename) as f:
            self._input = yaml.safe_load(f)

    @property
    def participants(self) -> Iterator[Participant]:
        for p in self._input["participants"]:
            yield Participant(**p)

    @property
    def scrapers(self) -> Iterator[Scraper]:
        for s in self._input["scrapers"]:
            yield Scraper(**s)

    @property
    def email_subject(self) -> str:
        return self._input["email"]["subject"]


@dataclass
class Participant:
    """A participant in the secret santa"""

    name: str
    email: str
    wishlist: Optional[Sequence[str]] = None

    def get_wishlist(self) -> Iterator[str]:
        if self.wishlist is None:
            return

        yield from self.wishlist

    def __hash__(self) -> int:
        return hash((self.name, self.email))

    def __eq__(self, __o: object) -> bool:

        if not isinstance(__o, Participant):
            return NotImplemented

        return self.name == __o.name and self.email == __o.email


@dataclass(repr=False)
class Pair:
    """A pair of participants, one giving to the other"""

    giver: Participant
    receiver: Participant

    def __repr__(self) -> str:
        return f"{self.giver.name} -> {self.receiver.name}"


class WishlistItem(Protocol):
    """What a wishlist item should look like"""

    def as_html(self, *args: Any, **kwargs: Any) -> str:
        ...


@dataclass
class LinkedItem:
    """A wishlist item that is just a link"""

    url: str

    def as_html(self) -> str:
        return f'<a href="{self.url}">{self.url}</a>'


@dataclass
class ScrapedItem:
    """A wishlist item where details can be scraped"""

    source: str
    code: str

    def as_html(self, scraper: Scraper) -> str:
        try:
            return self._try_scrape_html(scraper)
        except Exception as e:
            print(
                f"Failed to scrape {self.source}/{self.code}. Error: {e} "
                f"({scraper.target_url(self.code)})",
            )

        href = scraper.redirect_url(self.code)
        return f'<a href="{href}">{href}</a>'

    def _try_scrape_html(self, scraper: Scraper) -> str:
        details = scraper.scrape(self.code)
        return f'<a href="{scraper.redirect_url(self.code)}">{details}</a>'


@dataclass
class PlainTextItem:
    """Arbitrary text"""

    text: str

    def as_html(self) -> str:
        return self.text


@dataclass
class Scraper:
    source: str
    scrape_template: str
    href_template: str
    fields: dict[str, str]
    headers: Optional[dict[str, str]] = None

    def target_url(self, code: str) -> str:
        return self.scrape_template.format(code=code)

    def redirect_url(self, code: str) -> str:
        return self.href_template.format(code=code)

    def scrape(self, code: str) -> ScraperDetails:

        cache_file = self._cache_file(code)

        if os.environ.get("SANTA_DONT_SCRAPE") == "TRUE":
            raise Exception("Santa, don't scrape!")

        if os.path.exists(cache_file):
            print(f"Using cached scrape for {self.source} - {code}")
            with open(cache_file) as f:
                return ScraperDetails(**yaml.safe_load(f))

        resp = requests.get(self.target_url(code), headers=self.headers)
        soup = BeautifulSoup(resp.text, "html.parser")

        details_dict = {k: soup_select_or_none(soup, v) for k, v in self.fields.items()}
        details = ScraperDetails(**details_dict)

        with open(cache_file, "w") as f:
            yaml.safe_dump(details.as_dict(), f)

        return details

    def _cache_file(self, code: str) -> str:
        return f".scraper_cache/{self.source}.{code}.yaml"


ScrapedPrice = Union[str, float, None]


class ScraperDetails:
    title: str
    sale_price: float | None
    list_price: float | None

    def __init__(self, title: str | None, sale_price: ScrapedPrice, list_price: ScrapedPrice):

        if title is None:
            raise ValueError("Title is None")

        self.title = title.strip()
        self.sale_price = self._clean_price(sale_price)
        self.list_price = self._clean_price(list_price)

    def _clean_price(self, price_str: ScrapedPrice) -> float | None:

        if price_str is None:
            return None

        if isinstance(price_str, float):
            return price_str

        if price_str.count("$") > 1:
            price_str, _, _ = price_str.rpartition("$")

        if len(price_str) == 0:
            return None

        return float(price_str.strip().replace("$", ""))

    @property
    def title_abbr(self) -> str:
        MAX_LEN = 8
        if len(self.title.split(" ")) <= MAX_LEN:
            return self.title

        # if " - " in self.title:
        #     return self.title.partition(" - ")[0] + "..."

        return " ".join(self.title.split(" ")[:MAX_LEN]) + "..."

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "sale_price": self.sale_price,
            "list_price": self.list_price,
        }

    def __repr__(self) -> str:

        dolla_sale = dolla(self.sale_price)
        dolla_list = dolla(self.list_price)

        if self.list_price is None and self.sale_price is None:
            return self.title_abbr

        if self.sale_price is not None and self.list_price is not None:
            if self.sale_price < self.list_price:
                price_str = f"On sale for {dolla_sale}, usually {dolla_list}!"
            elif self.sale_price > self.list_price:
                price_str = f"Be aware, selling for {dolla_sale}, usually {dolla_list}."
            else:
                price_str = dolla_sale
        else:
            if self.sale_price is not None:
                price_str = dolla_sale
            else:
                price_str = dolla_list

        return f"{self.title_abbr} ({price_str})"


@dataclass
class Email:
    """An email to send"""

    to: str
    subject: str
    text_body: str = field(repr=False)
    img_body: str = field(repr=False)


@dataclass
class EmailFactory:
    """Factory for creating emails"""

    subject: str
    template: Template
    scrapers: Sequence[Scraper] = field(default_factory=list)

    def make_email(self, pair: Pair, test: bool = False) -> Email:

        if test:
            send_to = f"mbm2228+secret_santa__{pair.giver.name}@columbia.edu"
            send_to = send_to.replace("&", "").replace("  ", " ").replace(" ", "_").lower()
        else:
            send_to = pair.giver.email

        return Email(
            to=send_to,
            subject=self.subject,
            text_body=self._format_body(pair),
            img_body=self._format_body(pair, include_wishlist=False),
        )

    def _format_body(self, pair: Pair, include_wishlist: bool = True) -> str:

        if include_wishlist:
            wishlist = list(self._get_wishlist_items(pair.receiver))
        else:
            wishlist = None

        html = self.template.render(giver=pair.giver, receiver=pair.receiver, wishlist=wishlist)

        return html

    def _get_wishlist_items(self, participant: Participant) -> Iterator[str]:
        for item_str in participant.get_wishlist():
            item = parse_wishlist_item(item_str)

            if isinstance(item, ScrapedItem):
                scraper = next(s for s in self.scrapers if s.source == item.source)
                yield item.as_html(scraper)
            else:
                yield item.as_html()


@dataclass
class EmailAgent:
    username: str
    password: str
    host: str = "smtp.gmail.com"
    port: int = 587

    server: SMTP = field(init=False)

    def __post_init__(self) -> None:
        self.server = SMTP(host="smtp.gmail.com", port=587)
        self.server.connect(host="smtp.gmail.com", port=587)
        self.server.ehlo()
        self.server.starttls()
        self.server.ehlo()
        self.server.login(self.username, self.password)

    def send(self, email: Email, style: str) -> None:
        print(f"Sending email to {email.to}")

        msg = MIMEMultipart("mixed")
        msg["Subject"] = email.subject
        msg["From"] = "Santa Bot" + f"<{self.username}>"
        msg["To"] = email.to

        msg.attach(MIMEText(get_email_text(email.text_body, style), "html"))
        msg.attach(MIMEText(NOTE_TXT, "plain"))
        msg.attach(MIMEImage(get_email_image(email.img_body, style), "png"))

        self.server.sendmail(self.username, email.to, msg.as_string())


def get_pairs(people: Iterable[Participant]) -> Iterator[Pair]:

    people = list(people)
    random.shuffle(people)

    for i in range(len(people)):
        giver = people[i]
        receiver = people[(i + 1) % len(people)]
        yield Pair(giver, receiver)


def check_pairs(pairs: Sequence[Pair]) -> None:
    """Check no one is giving to themselves and everyone is giving and receiving once."""
    givers = set()
    receivers = set()

    for pair in pairs:
        if pair.giver == pair.receiver:
            raise ValueError(f"{pair.giver.name} is giving to themselves!")

        if pair.giver in givers:
            raise ValueError(f"{pair.giver.name} is giving twice!")

        if pair.receiver in receivers:
            raise ValueError(f"{pair.receiver.name} is receiving twice!")

        givers.add(pair.giver)
        receivers.add(pair.receiver)


def finalize_email(body: str, style: str) -> str:
    return f"<html><head>{FONTS}<style>{style}</style></head><body>{body}</body></html>"


def get_email_image(body: str, style: str) -> bytes:
    html = finalize_email(body, style)
    img_path = os.path.join("santa-email.png")

    hti = Html2Image()
    hti.screenshot(html_str=html, save_as=img_path, size=(1200, 600))

    with open(img_path, "rb") as f:
        img_bytes = f.read()

    os.remove(img_path)
    return img_bytes


def get_email_text(body: str, style: str) -> str:
    html = finalize_email(body, style)
    return markdown.markdown(html2text.html2text(html))


def parse_wishlist_item(item: str) -> WishlistItem:
    if m := SCRAPER_REGEX.search(item):  # perhaps future scrapers?
        return ScrapedItem(m.group("name"), m.group("code"))

    if URL_REGEX.match(item):
        return LinkedItem(url=item)

    return PlainTextItem(item)


def soup_select_or_none(soup: BeautifulSoup, selector: str) -> str | None:
    try:
        return soup.select_one(selector).text
    except AttributeError:
        return None


def dolla(dollars: float | None) -> str:
    if dollars is None:
        return "???"

    return f"${dollars:,.2f}"


def main(argv: Optional[Sequence[str]] = None) -> int:

    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", default="input.yaml", help="YAML file with people's info")
    parser.add_argument("-p", "--preview", action="store_true", help="Testing mode")
    parser.add_argument("-t", "--test", action="store_true", help="Send emails to test addresses")
    parser.add_argument("--no-scrape", action="store_true", help="Don't scrape")
    args = parser.parse_args(argv)
    print(args)

    if args.no_scrape:
        os.environ["SANTA_DONT_SCRAPE"] = "TRUE"

    if not os.path.exists(".scraper_cache"):
        os.mkdir(".scraper_cache")

    input = Input(args.input)
    people = list(input.participants)

    if len(people) != len(set(people)):
        raise ValueError("Duplicate names or emails found!")

    pairs = list(get_pairs(people))
    check_pairs(pairs)

    env = Environment(loader=FileSystemLoader(searchpath="./email"))
    body = env.get_template("template.html")
    style = env.get_template("style.css").render()

    email_factory = EmailFactory(input.email_subject, body, list(input.scrapers))
    emails = [email_factory.make_email(pair, args.test) for pair in pairs]

    if args.preview:
        with open("test.html", "w") as f:
            f.write(finalize_email("<hr>".join(e.img_body for e in emails), style))

        print()
        print("\n", "-" * 80, "\n", sep="")

        for email in emails:
            print("TO:", email.to)
            print("SUBJECT:", email.subject)
            print()
            print(get_email_text(email.text_body, style))

            print("\n", "-" * 80, "\n", sep="")

        return 0

    agent = EmailAgent(os.environ["SANTA_EMAIL"], os.environ["SANTA_PASSWORD"])

    print("Sending emails...")
    for email in emails:
        agent.send(email, style)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
