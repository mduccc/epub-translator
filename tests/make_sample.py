"""Tạo một EPUB 3 mẫu (có cả toc.ncx) với nhiều trường hợp khó để thử script."""
import base64, sys, zipfile

out = sys.argv[1]

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:3f2504e0-4f89-11d3-9a0c-0305e82c3301</dc:identifier>
    <dc:title>The Lighthouse Keeper's Daughter</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch3" href="ch%203.xhtml" media-type="application/xhtml+xml"/>
    <item id="img" href="img.png" media-type="image/png"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="nav"/>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
    <itemref idref="ch3"/>
  </spine>
</package>"""

nav = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc" id="toc">
  <h1>Contents</h1>
  <ol>
    <li><a href="ch1.xhtml">Chapter One: The Storm</a>
      <ol><li><a href="ch1.xhtml#p5">The Letter</a></li></ol>
    </li>
    <li><a href="ch2.xhtml">Chapter Two: Morning Tide</a></li>
    <li><a href="ch%203.xhtml">Chapter Three: Harbour Lights</a></li>
  </ol>
</nav>
</body>
</html>"""

ncx = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:3f2504e0-4f89-11d3-9a0c-0305e82c3301"/></head>
  <docTitle><text>The Lighthouse Keeper's Daughter</text></docTitle>
  <navMap>
    <navPoint id="n1" playOrder="1"><navLabel><text>Chapter One: The Storm</text></navLabel><content src="ch1.xhtml"/></navPoint>
    <navPoint id="n2" playOrder="2"><navLabel><text>Chapter Two: Morning Tide</text></navLabel><content src="ch2.xhtml"/></navPoint>
    <navPoint id="n3" playOrder="3"><navLabel><text>Chapter Three: Harbour Lights</text></navLabel><content src="ch%203.xhtml"/></navPoint>
  </navMap>
</ncx>"""

css = """@font-face { font-family: "Fancy"; src: url(fancy.otf); }
body { font-family: "Fancy", serif; }
p { text-indent: 1.5em; margin: 0; }
"""

ch1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><title>Chapter One</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
<section epub:type="chapter">
<h1 id="ch1-title">Chapter One: The Storm</h1>
<p>The wind had been rising since <i>noon</i>, and by evening the whole island seemed to <b>tremble</b> under it.</p>
<p>Mara stood at the window of the lamp room, watching the grey water heave and break against the rocks below.<a href="#n1" epub:type="noteref"><sup>1</sup></a></p>
<p>"You should come away from there," her father said, without looking up from his ledger.<span epub:type="pagebreak" id="page2" title="2"/></p>
<p>She did not move. MERGE_ME Somewhere out in that darkness a ship was trying to find the harbour.</p>
<p>Her father sighed and closed the book. He was a patient man, but the sea had taught him not to waste words.</p>
<p><a id="p5"/>The letter had arrived three days earlier, folded twice and sealed with blue wax.</p>
<p class="center">* * *</p>
<div class="epigraph">Some winds carry <i>news</i>, the old saying went.<p>Others carry only salt.</p></div>
<p>It said simply that her brother was coming home,&#160;and that he would not be coming alone. TAGFLAKY <i>Not alone</i>, Mara repeated.</p>
<p>She read the line again by candlelight,<br/>then a third time,<br/>as if the words might change their minds.</p>
<p>The ink had run in one corner, as though the paper had been held out in the rain. CJK</p>
<aside epub:type="footnote" id="n1"><p>1. The lamp room sits at the very top of the tower.</p></aside>
</section>
</body>
</html>"""

ch2 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head><title>Chapter Two</title></head>
<body>
<h1>Chapter Two: Morning Tide</h1>
<p>Morning came pale and exhausted&mdash;the kind of morning that apologises for the night before.</p>
<p>Mrs.&nbsp;Hale from the village brought bread and news, in that order, and both were still warm. DROPTAG She had <em>always</em> believed news should be delivered <em>hot</em>.</p>
<blockquote><p>"A ship went down off the northern point," she said. "But the crew made it to shore."</p></blockquote>
<pre>The tide comes in,
    the tide goes out,
and nobody asks it why.</pre>
<ul>
<li>Two loaves of bread</li>
<li>A jar of plum jam</li>
<li>One rumour, <i>unconfirmed</i></li>
</ul>
<table><tr><th>Day</th><th>Weather</th></tr><tr><td>Monday</td><td>Storm from the west</td></tr></table>
<p>NEVER This paragraph is designed to always fail in the mock server, no matter how many retries happen.</p>
</body>
</html>"""

ch3 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
<head><title>Chapter Three</title></head>
<body>
<h1>Chapter Three: Harbour Lights</h1>
<figure><img src="img.png" alt="A small harbour at dusk"/><figcaption>The harbour at dusk, from the tower.</figcaption></figure>
<p>When the boat finally rounded the point, Mara was the first to see it.</p>
<p>When the boat finally rounded the point, Mara was the first to see it.</p>
<p>Her brother stood at the bow, one hand raised, and beside him was a stranger in a long green coat.</p>
</body>
</html>"""

with zipfile.ZipFile(out, "w") as z:
    z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
    for name, data in [("content.opf", opf), ("nav.xhtml", nav), ("toc.ncx", ncx),
                       ("style.css", css), ("ch1.xhtml", ch1), ("ch2.xhtml", ch2),
                       ("ch 3.xhtml", ch3)]:
        z.writestr("OEBPS/" + name, data, compress_type=zipfile.ZIP_DEFLATED)
    z.writestr("OEBPS/img.png", PNG)
print("wrote", out)
