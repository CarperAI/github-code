import scrapy
import json
import pathlib
import re
import os
import sys
import random
import tarfile, io

from urllib.parse import urlparse

from scrapy.crawler import CrawlerProcess
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.internet.error import DNSLookupError
from twisted.internet.error import TimeoutError

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class DiscourseSpider(scrapy.Spider):
    name = "discourse"
    headers = {
            #'Content-Type': 'application/json',
            'Accept': 'application/json'
            }
    user_agent = 'Mozilla/5.0 (compatible; Carper-GooseBot/8000; +https://carper.ai/)'
    download_delay = 0

    scheduler_priority_queue = 'scrapy.pqueues.DownloaderAwarePriorityQueue'
    #concurrent_requests_per_domain = 2
    concurrent_requests = 1000


    scrapelatest = False
    scrapetop = False
    scrapecategories = False
    scrapetopics = False
    scrapeindex = True

    usetarballs = False

    failures = {}

    def start_requests(self):
        pathlib.Path('discourse').mkdir(parents=True, exist_ok=True)
        # TODO - need some way of specifying which crawl index file to use
        with open('discourse/index.json', 'r') as indexfd:
            urls = json.loads(indexfd.read())
            random.shuffle(urls)
            for url in urls:
                if self.scrapelatest:
                    yield self.create_request(url, 10)
                if self.scrapetop:
                    yield self.create_request(url + 'top', 10)
                if self.scrapecategories:
                    yield self.create_request(url + 'categories', 10)
                if self.scrapeindex:
                    yield self.create_request(url + 'site', 20)
        
    def create_request(self, url, priority=0):
        return scrapy.Request(url=url, callback=self.parse, headers=self.headers, errback=self.handle_errback, priority=priority) # + random.randint(0, 10))

    def parse(self, response):
        try:
            jsondata = json.loads(response.body)
        except ValueError as e:
            # log failure
            print("[" + bcolors.WARNING + 'WARNING' + bcolors.ENDC + "]\tfailed to parse JSON at %s" % (response.url))
            self.write_failure(response.url, 'json_error')
            return

        #print(jsondata)
        #print(response.request.headers)
        #m = re.match(r"^(https?://)([^/]+)(/.*?)?$", response.url)

        #if not m:
        #    print("Warning: couldn't understand URL", response.url)
        #    return

        #protocol = m.group(1)
        #domain = m.group(2)
        #urlpath = m.group(3)
        #filename = m.group(4) or urlpath

        url = urlparse(response.url)
        protocol = url.scheme + '://'
        domain = url.netloc
        urlpath = url.path
        if url.query:
            urlpath += url.query

        if urlpath[-1] == '/':
            urlpath += 'index'

        baseurl = protocol + domain + urlpath

        datapath = 'discourse/%s/%s/' % (domain, urlpath)

        if 'category_list' in jsondata:
            #print ('domain: %s\turlpath: %s\tfilename: %s' % (domain, urlpath, filename))
            self.write_file(domain, urlpath, response.text)
            for category in jsondata['category_list']['categories']:
                if self.scrapetopics:
                    if 'topic_url' in category and category['topic_url'] is not None:
                        topicurl = '%s%s%s' % (protocol, domain, category['topic_url'])
                        yield self.create_request(topicurl)
                    categoryurl = '%s%s/c/%s/%d' % (protocol, domain, category['slug'], category['id'])
                    yield self.create_request(categoryurl, 10)
                if self.scrapecategories and 'subcategory_ids' in category:
                    for categoryid in category['subcategory_ids']:
                        subcategoryurl = '%s%s/c/%d' % (protocol, domain, categoryid)
                        #print('add subcategory', subcategoryurl)
                        yield self.create_request(subcategoryurl, 10)
                    

        if 'topic_list' in jsondata:
            self.write_file(domain, urlpath, response.text)
            if 'more_topics_url' in jsondata['topic_list']:
                nexturl = protocol + domain + jsondata['topic_list']['more_topics_url']
                #print('Add next page URL', nexturl, self.headers)
                yield self.create_request(nexturl, 5)

            if self.scrapetopics:
                topics = jsondata['topic_list']['topics']
                for topic in topics:
                    crawlfname = datapath + '/t/%s/%d' % (topic['slug'], topic['id'])
                    if not os.path.isfile(crawlfname): 
                        # TODO - to facilitate continuous crawling, we probably want to check the last crawled time, and refresh if our list data indicates new posts
                        # As implemented, this is just a one-shot crawl that can be resumed. It'll grab new topics, but not refresh any changed ones
                        topicurl = protocol + domain + '/t/%s/%d' % (topic['slug'], topic['id'])
                        #print('New topicurl: ' + topicurl)
                        yield self.create_request(topicurl)
                    #else:
                    #    print('Skipping topic %s, already exists' % topic['slug'])
        if 'post_stream' in jsondata:
            print('[' + bcolors.OKGREEN + ' Saved ' + bcolors.ENDC + ']\t%-40s %-60s' % (domain, jsondata['fancy_title']))
            #crawlfname = datapath + filename + '.json'
            #pathlib.Path(datapath).mkdir(parents=True, exist_ok=True)
            #with open(crawlfname, 'w') as fd:
            #    fd.write(response.text)
            self.write_file(domain, urlpath, response.text)
        if 'categories' in jsondata:
            #print('got full list of categories, probably the site index', jsondata)
            self.write_file(domain, urlpath, response.text)
    def write_file(self, domain, filepath, contents):
        if domain == '' or not self.usetarballs:
            crawlfname = 'discourse/%s%s' % (domain, filepath)
            datapath = os.path.dirname(crawlfname)

            pathlib.Path(datapath).mkdir(parents=True, exist_ok=True)

            with open(crawlfname, 'w') as fd:
                #print("write file", crawlfname)
                fd.write(contents)
        else:
            tarballname = 'discourse/%s.tar' % domain
            if len(contents) > 0:
                tarinfo = tarfile.TarInfo(filepath)
                encoded = contents.encode()
                tarinfo.size = len(encoded)
                file = io.BytesIO(encoded)
                try:
                    tar = tarfile.open(tarballname, 'a')
                    tar.addfile(tarinfo, file)
                    tar.close()
                    print("write tarball", tarballname)
                except tarfile.ReadError:
                    print("oh no", filepath)
                    pass

            else:
                print('wtf why', tarballname, filepath)
    def handle_errback(self, failure):
        if failure.check(HttpError):
            print("["  + bcolors.WARNING + 'WARNING' + bcolors.ENDC + "]\tfailed to fetch URL %s" % (failure.value.response.url))
            self.write_failure(failure.value.response.url, 'http_error')
        elif failure.check(DNSLookupError):
            print("[" + bcolors.WARNING + 'WARNING' + bcolors.ENDC + "]\tDNS failure resolving %s" % (failure.request.url))
            self.write_failure(failure.request.url, 'dns_error')
        elif failure.check(TimeoutError):
            print("[" + bcolors.WARNING + 'WARNING' + bcolors.ENDC + "]\tTimed out fetching %s" % (failure.request.url))
            self.write_failure(failure.request.url, 'timeout_error')
    def write_failure(self, url, reason):
        self.failures[url] = reason
        #print("[FAILURE] %s (%s)" % (url, reason))
        self.write_file('', 'failures.json', json.dumps(self.failures, indent=2))



class DiscourseSummarySpider(DiscourseSpider):
    scrapelatest = False
    scrapetop = False
    scrapecategories = False
    scrapetopics = False
    scrapeindex = True

class DiscourseTopicSpider(DiscourseSpider):
    scrapelatest = True
    scrapetop = True
    scrapecategories = True
    scrapetopics = True
    scrapeindex = False


def generateCrawlSummary():
    try :
        with open('discourse/failures.json') as failurefd:
            failures = json.loads(failurefd.read())
        with open('discourse/index.json') as index:
            sites = json.loads(index.read())
    except FileNotFoundError:
        print("[ " + bcolors.FAIL + 'ERROR ' + bcolors.ENDC + '] couldn\'t read index or failure files')
        return
    faildomains = {}
    for failurl in failures:
            m = re.match(r"^(https?://)([^/]+)(/.*?)$", failurl)
            if m:
                protocol = m.group(1)
                domain = m.group(2)
                faildomains[domain] = failures[failurl]

    if failures and sites:
        crawlsummary = {
            '_totals': {
                'sites': 0,
                'sites_valid': 0,
                'topic_count': 0,
                'post_count': 0,
                'category_count': 0,
                }
            }
        print('Collecting crawl stats...')
        for site in sites:
            url = urlparse(site)
            protocol = url.scheme + '://'
            domain = url.netloc
            urlpath = url.path
            if url.query:
                urlpath += url.query
            #m = re.match(r"^(https?://)([^/]+)(/.*?)$", site)
            #if m:
            #    protocol = m.group(1)
            #    domain = m.group(2)

            if True:
                crawlsummary[domain] = {}
                tarballname = 'discourse/%s.tar' % domain
                fname = 'discourse/%s/site' % domain
                crawlsummary['_totals']['sites'] += 1
                crawlsummary[domain]['topic_count'] = 0
                crawlsummary[domain]['post_count'] = 0
                crawlsummary[domain]['category_count'] = 0
                crawlsummary[domain]['categories'] = {}

                if domain in faildomains:
                    crawlsummary[domain]['failure'] = faildomains[domain]

                if os.path.isfile(fname): 
                    #print('open file', fname)
                    with open(fname, 'r') as sitefd:
                        crawlsummary['_totals']['sites_valid'] += 1
                        sitejson = json.loads(sitefd.read())
                        for category in sitejson['categories']:
                            crawlsummary[domain]['categories'][category['slug']] = category
                            crawlsummary[domain]['topic_count'] = crawlsummary[domain]['topic_count'] + category['topic_count']
                            crawlsummary[domain]['post_count'] = crawlsummary[domain]['post_count'] + category['post_count']
                            crawlsummary[domain]['category_count'] += 1
                elif os.path.isfile(tarballname): 
                    print("open tarball", tarballname)
                    try:
                        tar = tarfile.open(tarballname, 'r')
                        extractfile = (urlpath or '/') + 'site'
                        try:
                            tarreader = tar.extractfile(extractfile)
                            sitejson = json.loads(tarreader.read(6553600))
                            for category in sitejson['categories']:
                                crawlsummary[domain]['categories'][category['slug']] = category
                                crawlsummary[domain]['topic_count'] = crawlsummary[domain]['topic_count'] + category['topic_count']
                                crawlsummary[domain]['post_count'] = crawlsummary[domain]['post_count'] + category['post_count']
                                crawlsummary[domain]['category_count'] += 1
                        except KeyError:
                            pass
                        tar.close()
                    except tarfile.ReadError:
                        print("oh no", filepath)
                        pass
                crawlsummary['_totals']['topic_count'] += crawlsummary[domain]['topic_count']
                crawlsummary['_totals']['post_count'] += crawlsummary[domain]['post_count']
                crawlsummary['_totals']['category_count'] += crawlsummary[domain]['category_count']
        
        print('Writing...')
        with open('discourse/crawlsummary.json', 'w') as crawlsummaryfd:
            crawlsummaryfd.write(json.dumps(crawlsummary, indent=2))
        print('Done.  Crawl summary written to discourse/crawlsummary.json')
        print(crawlsummary['_totals'])


if __name__ == "__main__":
    process = CrawlerProcess()
    if len(sys.argv) == 1:
        print("Usage: %s [index|topics] <sourcefile>" % (sys.argv[0]))
        exit(0)
    crawltype = sys.argv[1]


    if crawltype == 'index':
        process.crawl(DiscourseSpider)
        process.start()
        generateIndexStats()
    elif crawltype == 'topics':
        process.crawl(DiscourseTopicSpider)
        process.start()

