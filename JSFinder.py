#!/usr/bin/env python3
# coding: utf-8
# By Threezh1
# Upgraded: HTTP status codes + colored output + multi-threaded probing
# https://threezh1.github.io/

import requests, argparse, sys, re
from requests.packages import urllib3
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# ------------------------------ Dependencies ------------------------------
try:
    import colorama
    colorama.init(autoreset=True)
    HAS_COLORAMA = True
except ImportError:
    HAS_COLORAMA = False

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ------------------------------ Color helpers -----------------------------
class c:
    RED     = '\033[91m'
    GREEN   = '\033[92m'
    YELLOW  = '\033[93m'
    BLUE    = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN    = '\033[96m'
    GRAY    = '\033[90m'
    BOLD    = '\033[1m'
    END     = '\033[0m'

USE_COLOR = True

def colorize(text, *colors):
    if not USE_COLOR:
        return text
    return "".join(colors) + text + c.END

def status_color(status):
    """Return the color code that matches a HTTP status code."""
    if status is None:           # network error
        return c.MAGENTA
    if 200 <= status < 300:
        return c.GREEN
    if 300 <= status < 400:
        return c.CYAN
    if 400 <= status < 500:
        return c.YELLOW
    if status >= 500:
        return c.RED + c.BOLD
    return c.MAGENTA

# ------------------------------ HTTP session ------------------------------
session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64)
session.mount('http://', _adapter)
session.mount('https://', _adapter)
session.headers.update({"User-Agent": UA})

def build_headers():
    headers = {"User-Agent": UA}
    if args.cookie:
        headers["Cookie"] = args.cookie
    return headers

def check_status(url):
    """
    Probe a single URL and return (url, status_code).
    Strategy: HEAD first (fast); fall back to GET when the server rejects HEAD.
    status_code is None when the request fails entirely.
    """
    headers = build_headers()
    try:
        resp = session.head(url, headers=headers, timeout=args.timeout,
                            verify=False, allow_redirects=True)
        code = resp.status_code
        if code in (405, 501):                       # HEAD not allowed -> GET
            resp = session.get(url, headers=headers, timeout=args.timeout,
                               verify=False, allow_redirects=True, stream=True)
            resp.close()
            code = resp.status_code
        return (url, code)
    except requests.exceptions.RequestException:
        try:
            resp = session.get(url, headers=headers, timeout=args.timeout,
                               verify=False, allow_redirects=True, stream=True)
            resp.close()
            return (url, resp.status_code)
        except requests.exceptions.RequestException:
            return (url, None)

def check_all_status(urls):
    """Probe every absolute URL concurrently. Returns {url: status_code|None}."""
    results = {}
    to_check = [u for u in urls if re.match(r'^https?://', u, re.I)]
    total = len(to_check)
    if total == 0:
        return results
    completed = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.threads)) as ex:
            futures = {ex.submit(check_status, u): u for u in to_check}
            for fut in as_completed(futures):
                u, status = fut.result()
                results[u] = status
                completed += 1
                if sys.stdout.isatty():
                    sys.stdout.write("\r\033[K  {} [{}/{}]".format(
                        colorize("checking", c.GRAY), completed, total))
                    sys.stdout.flush()
    except KeyboardInterrupt:
        print(colorize("\nInterrupted by user, keeping partial results.", c.YELLOW))
    if sys.stdout.isatty():
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    return results

# ------------------------------ Arguments ---------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        epilog='\tExample: \r\npython ' + sys.argv[0] + " -u http://www.baidu.com")
    parser.add_argument("-u", "--url", help="The website")
    parser.add_argument("-c", "--cookie", help="The website cookie")
    parser.add_argument("-f", "--file", help="The file contains url or js")
    parser.add_argument("-ou", "--outputurl", help="Output file name. ")
    parser.add_argument("-os", "--outputsubdomain", help="Output file name. ")
    parser.add_argument("-j", "--js", help="Find in js file", action="store_true")
    parser.add_argument("-d", "--deep", help="Deep find", action="store_true")
    parser.add_argument("-t", "--threads", type=int, default=20,
                        help="Threads for status probing (default 20)")
    parser.add_argument("--timeout", type=int, default=5,
                        help="Request timeout in seconds (default 5)")
    parser.add_argument("--no-status", action="store_true",
                        help="Skip HTTP status code probing")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colored output")
    return parser.parse_args()

# ------------------------------ Core logic --------------------------------
# Regular expression comes from https://github.com/GerbenJavado/LinkFinder
def extract_URL(JS):
	pattern_raw = r"""
	  (?:"|')                               # Start newline delimiter
	  (
	    ((?:[a-zA-Z]{1,10}://|//)           # Match a scheme [a-Z]*1-10 or //
	    [^"'/]{1,}\.                        # Match a domainname (any character + dot)
	    [a-zA-Z]{2,}[^"']{0,})              # The domainextension and/or path
	    |
	    ((?:/|\.\./|\./)                    # Start with /,../,./
	    [^"'><,;| *()(%%$^/\\\[\]]          # Next character can't be...
	    [^"'><,;|()]{1,})                   # Rest of the characters can't be
	    |
	    ([a-zA-Z0-9_\-/]{1,}/               # Relative endpoint with /
	    [a-zA-Z0-9_\-/]{1,}                 # Resource name
	    \.(?:[a-zA-Z]{1,4}|action)          # Rest + extension (length 1-4 or action)
	    (?:[\?|/][^"|']{0,}|))              # ? mark with parameters
	    |
	    ([a-zA-Z0-9_\-]{1,}                 # filename
	    \.(?:php|asp|aspx|jsp|json|
	         action|html|js|txt|xml)             # . + extension
	    (?:\?[^"|']{0,}|))                  # ? mark with parameters
	  )
	  (?:"|')                               # End newline delimiter
	"""
	pattern = re.compile(pattern_raw, re.VERBOSE)
	result = re.finditer(pattern, str(JS))
	if result == None:
		return None
	js_url = []
	return [match.group().strip('"').strip("'") for match in result
		if match.group() not in js_url]

# Get the page source
def Extract_html(URL):
	header = {"User-Agent": UA, "Cookie": args.cookie}
	try:
		raw = requests.get(URL, headers = header, timeout=5, verify=False)
		raw = raw.content.decode("utf-8", "ignore")
		return raw
	except:
		return None

# Handling relative URLs
def process_url(URL, re_URL):
	black_url = ["javascript:"]	# Add some keyword for filter url.
	URL_raw = urlparse(URL)
	ab_URL = URL_raw.netloc
	host_URL = URL_raw.scheme
	if re_URL[0:2] == "//":
		result = host_URL  + ":" + re_URL
	elif re_URL[0:4] == "http":
		result = re_URL
	elif re_URL[0:2] != "//" and re_URL not in black_url:
		if re_URL[0:1] == "/":
			result = host_URL + "://" + ab_URL + re_URL
		else:
			if re_URL[0:1] == ".":
				if re_URL[0:2] == "..":
					result = host_URL + "://" + ab_URL + re_URL[2:]
				else:
					result = host_URL + "://" + ab_URL + re_URL[1:]
			else:
				result = host_URL + "://" + ab_URL + "/" + re_URL
	else:
		result = URL
	return result

def find_last(string,str):
	positions = []
	last_position=-1
	while True:
		position = string.find(str,last_position+1)
		if position == -1:break
		last_position = position
		positions.append(position)
	return positions

def find_by_url(url, js = False):
	if js == False:
		try:
			print("url:" + url)
		except:
			print("Please specify a URL like https://www.baidu.com")
		html_raw = Extract_html(url)
		if html_raw == None:
			print("Fail to access " + url)
			return None
		#print(html_raw)
		html = BeautifulSoup(html_raw, "html.parser")
		html_scripts = html.find_all("script")
		script_array = {}
		script_temp = ""
		for html_script in html_scripts:
			script_src = html_script.get("src")
			if script_src == None:
				script_temp += html_script.get_text() + "\n"
			else:
				purl = process_url(url, script_src)
				script_array[purl] = Extract_html(purl)
		script_array[url] = script_temp
		allurls = []
		for script in script_array:
			#print(script)
			temp_urls = extract_URL(script_array[script])
			if len(temp_urls) == 0: continue
			for temp_url in temp_urls:
				allurls.append(process_url(script, temp_url))
		result = []
		for singerurl in allurls:
			url_raw = urlparse(url)
			domain = url_raw.netloc
			positions = find_last(domain, ".")
			miandomain = domain
			if len(positions) > 1:miandomain = domain[positions[-2] + 1:]
			#print(miandomain)
			suburl = urlparse(singerurl)
			subdomain = suburl.netloc
			#print(singerurl)
			if miandomain in subdomain or subdomain.strip() == "":
				if singerurl.strip() not in result:
					result.append(singerurl)
		return result
	return sorted(set(extract_URL(Extract_html(url)))) or None


def find_subdomain(urls, mainurl):
	url_raw = urlparse(mainurl)
	domain = url_raw.netloc
	miandomain = domain
	positions = find_last(domain, ".")
	if len(positions) > 1:miandomain = domain[positions[-2] + 1:]
	subdomains = []
	for url in urls:
		suburl = urlparse(url)
		subdomain = suburl.netloc
		#print(subdomain)
		if subdomain.strip() == "": continue
		if miandomain in subdomain:
			if subdomain not in subdomains:
				subdomains.append(subdomain)
	return subdomains

def find_by_url_deep(url):
	html_raw = Extract_html(url)
	if html_raw == None:
		print("Fail to access " + url)
		return None
	html = BeautifulSoup(html_raw, "html.parser")
	html_as = html.find_all("a")
	links = []
	for html_a in html_as:
		src = html_a.get("href")
		if src == "" or src == None: continue
		link = process_url(url, src)
		if link not in links:
			links.append(link)
	if links == []: return None
	print("ALL Find " + str(len(links)) + " links")
	urls = []
	i = len(links)
	for link in links:
		temp_urls = find_by_url(link)
		if temp_urls == None: continue
		print("Remaining " + str(i) + " | Find " + str(len(temp_urls)) + " URL in " + link)
		for temp_url in temp_urls:
			if temp_url not in urls:
				urls.append(temp_url)
		i -= 1
	return urls


def find_by_file(file_path, js=False):
	with open(file_path, "r") as fobject:
		links = fobject.read().split("\n")
	if links == []: return None
	print("ALL Find " + str(len(links)) + " links")
	urls = []
	i = len(links)
	for link in links:
		if js == False:
			temp_urls = find_by_url(link)
		else:
			temp_urls = find_by_url(link, js=True)
		if temp_urls == None: continue
		print(str(i) + " Find " + str(len(temp_urls)) + " URL in " + link)
		for temp_url in temp_urls:
			if temp_url not in urls:
				urls.append(temp_url)
		i -= 1
	return urls

# ------------------------------ Banner ------------------------------------
def banner():
    print(colorize("=" * 58, c.CYAN))
    print(colorize("  JSFinder  ", c.BOLD + c.GREEN) +
          colorize("Upgraded Edition", c.BOLD))
    print(colorize("  URL & subdomain extraction + HTTP status + color", c.GRAY))
    print(colorize("=" * 58, c.CYAN))

# ------------------------------ Status label ------------------------------
def status_label(status):
    """Return the bracketed status label, e.g. '[200]', '[ERR]', '[ - ]'."""
    if status is None:
        return "[ERR]"
    return "[{}]".format(status)

# ------------------------------ Result output -----------------------------
def print_summary(status_results):
    if args.no_status:
        return
    counts = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "ERR": 0}
    for s in status_results.values():
        if s is None:               counts["ERR"] += 1
        elif 200 <= s < 300:        counts["2xx"] += 1
        elif 300 <= s < 400:        counts["3xx"] += 1
        elif 400 <= s < 500:        counts["4xx"] += 1
        else:                       counts["5xx"] += 1
    print(colorize("  Status summary:", c.BOLD))
    print("    {} {} {} {} {}".format(
        colorize("2xx:" + str(counts["2xx"]), c.GREEN),
        colorize("3xx:" + str(counts["3xx"]), c.CYAN),
        colorize("4xx:" + str(counts["4xx"]), c.YELLOW),
        colorize("5xx:" + str(counts["5xx"]), c.RED + c.BOLD),
        colorize("ERR:" + str(counts["ERR"]), c.MAGENTA),
    ))

def giveresult(urls, domain):
    if urls == None or len(urls) == 0:
        print(colorize("No URL found.", c.YELLOW))
        return

    # ---- Probe HTTP status codes ----
    if args.no_status:
        status_results = {}
    else:
        print(colorize("Probing HTTP status codes for {} URLs ...".format(len(urls)), c.BOLD))
        status_results = check_all_status(urls)

    # ---- Print URL list with status + color ----
    print(colorize("Find " + str(len(urls)) + " URL:", c.BOLD + c.GREEN))
    content_url = ""
    for url in urls:
        if url in status_results:
            status = status_results[url]
            label = status_label(status)
            col = status_color(status)
        else:                               # not probed (relative URL or --no-status)
            label = "[ - ]"
            col = c.GRAY
        print("  " + colorize(label, col) + " " + url)
        content_url += label + " " + url + "\n"

    print_summary(status_results)

    # ---- Subdomains ----
    subdomains = find_subdomain(urls, domain)
    print(colorize("\nFind " + str(len(subdomains)) + " Subdomain:", c.BOLD + c.GREEN))
    content_subdomain = ""
    for subdomain in subdomains:
        print("  " + colorize(subdomain, c.CYAN))
        content_subdomain += subdomain + "\n"

    # ---- Write files ----
    if args.outputurl != None:
        with open(args.outputurl, "a", encoding='utf-8') as fobject:
            fobject.write(content_url)
        print("\n" + colorize("Output " + str(len(urls)) + " urls", c.GREEN))
        print("Path:" + args.outputurl)
    if args.outputsubdomain != None:
        with open(args.outputsubdomain, "a", encoding='utf-8') as fobject:
            fobject.write(content_subdomain)
        print(colorize("Output " + str(len(subdomains)) + " subdomains", c.GREEN))
        print("Path:" + args.outputsubdomain)

# ------------------------------ Main --------------------------------------
if __name__ == "__main__":
    urllib3.disable_warnings()
    args = parse_args()
    USE_COLOR = (not args.no_color) and sys.stdout.isatty()

    banner()

    if args.file == None:
        if args.deep is not True:
            urls = find_by_url(args.url)
            if urls == None:
                sys.exit(0)
            giveresult(urls, args.url)
        else:
            urls = find_by_url_deep(args.url)
            if urls == None:
                sys.exit(0)
            giveresult(urls, args.url)
    else:
        if args.js is not True:
            urls = find_by_file(args.file)
        else:
            urls = find_by_file(args.file, js=True)
        if urls == None or len(urls) == 0:
            print(colorize("No URL found.", c.YELLOW))
            sys.exit(0)
        giveresult(urls, urls[0])
