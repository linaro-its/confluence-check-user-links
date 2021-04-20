""" Scan all of the pages in a space looking for user links """

import json
import os
import re
import sys
from io import StringIO

import requests
from json_minify import json_minify
from ldap3 import SUBTREE, Connection
from requests.auth import HTTPBasicAuth

CONFIG = None

# Example storage format we are looking for:
#
# <ac:link><ri:user ri:userkey="8a09c088436c2b310143b4ea8e330037" /></ac:link>
#
# or (all as one line):
#
# <ac:link><ri:user ri:userkey="8a09c088423dfa2b01423dfaca2d0264" />
# <ac:plain-text-link-body><![CDATA[Bill Fischofer]]></ac:plain-text-link-body>
# </ac:link>
#
# or:
# <ac:link><ri:user ri:userkey="8a09c088423dfa2b01423dfac8ba01d2" />
# <ac:link-body><span style=\"color: rgb(0,0,0);\">Vicky Janicki</span></ac:link-body>
# </ac:link>
#
MACRO_START = (
    '<ac:link><ri:user ri:userkey='
)

MACRO_END_1 = (
    '/></ac:link>'
)

MACRO_END_2A = (
    '/><ac:plain-text-link-body><![CDATA['
)

MACRO_END_2B = (
    ']]></ac:plain-text-link-body></ac:link>'
)

MACRO_END_3A = (
    '/><ac:link-body>'
)

MACRO_END_3B = (
    '</ac:link-body></ac:link>'
)

def load_config():
    """ Load the config file """
    global CONFIG
    basedir = os.path.dirname(os.path.dirname(__file__))
    config_file = os.path.join(basedir, "config.jsonc")
    try:
        with open(config_file) as handle:
            CONFIG = json.loads(json_minify(handle.read()))
    except json.decoder.JSONDecodeError as exc:
        sys.exit("Unable to decode config file successfully")

def get_auth(user_key, pw_key):
    """ Return HTTP auth """
    username = CONFIG[user_key]
    password = CONFIG[pw_key]
    return HTTPBasicAuth(username, password)

def get_pagetypes(server, auth, space_key):
    """ Return a list of page types used on this space """
    response = requests.get(
        "%s/rest/api/space/%s/content?limit=1" % (server, space_key),
        auth=auth)
    result = []
    data = response.json()
    for foo in data:
        if foo != "_links":
            result.append(foo)
    return result

def get_all_pages(server, auth, space_key, page_type):
    """ Return a dict of page names and their URLs """
    all_pages = {}
    # There is a bug in the Server API which means that pagination
    # doesn't necessarily find all of the pages! Hence set the limit
    # as high as it can be.
    url = "%s/rest/api/space/%s/content/%s?limit=1000" % (server, space_key, page_type)
    while True:
        result = requests.get(url, auth=auth)
        if result.status_code != 200:
            print(url)
            print(result.text)
            sys.exit("Failed to retrieve pages from %s for %s" % (server, space_key))
        data = result.json()
        add_pages(all_pages, data)
        if "next" in data["_links"]:
            url = "%s%s" % (server, data["_links"]["next"])
        else:
            break
    return all_pages

def add_pages(pages_dict, data):
    """ Add the pages to the dict """
    results = data["results"]
    for page in results:
        pages_dict[page["title"]] = page["_links"]["self"]

def lookup_user(reference, server_uri, auth):
    """ Get display name for the specified user """
    # Need to strip the double-quotes from the reference
    reference = reference.strip().replace('"', '')
    url = "%s/rest/api/user?key=%s" % (server_uri, reference)
    try:
        result = requests.get(url, auth=auth)
    except Exception as exc:
        sys.exit("Exception while accessing %s: %s" % (url, exc))
    if result.status_code != 200:
        print(result.text)
        sys.exit("Failed to retrieve user '%s'" % reference)
    data = result.json()
    display_name = data["displayName"]
    return display_name, "Unknown User" not in display_name

def process_link_type_1(buffer, body, server_uri, auth):
    """ Process the first type of link """
    link_end_1 = body.find(MACRO_END_1)
    reference = body[:link_end_1]
    name, active_user = lookup_user(reference, server_uri, auth)
    if active_user:
        # Copy the entire macro over ...
        buffer.write(MACRO_START)
        buffer.write(body[:link_end_1 + len(MACRO_END_1)])
    # Remove from the body
    body = body[link_end_1 + len(MACRO_END_1):]
    return body, name, active_user

def process_link_type_2(buffer, body, server_uri, auth):
    """ Process the second type of link """
    link_end_2a = body.find(MACRO_END_2A)
    link_end_2b = body.find(MACRO_END_2B)
    reference = body[:link_end_2a]
    name, active_user = lookup_user(reference, server_uri, auth)
    if active_user:
        # Copy the entire macro over ...
        buffer.write(MACRO_START)
        buffer.write(body[:link_end_2b +len(MACRO_END_2B)])
    # Remove from the body
    body = body[link_end_2b + len(MACRO_END_2B):]
    return body, name, active_user

def process_link_type_3(buffer, body, server_uri, auth):
    """ Process the third type of link """
    link_end_3a = body.find(MACRO_END_3A)
    link_end_3b = body.find(MACRO_END_3B)
    reference = body[:link_end_3a]
    name, active_user = lookup_user(reference, server_uri, auth)
    if active_user:
        # Copy the entire macro over ...
        buffer.write(MACRO_START)
        buffer.write(body[:link_end_3b +len(MACRO_END_3B)])
    # Remove from the body
    body = body[link_end_3b + len(MACRO_END_3B):]
    return body, name, active_user

def which_link_type(body):
    """ Which type of link have we found? """
    link_end_1 = body.find(MACRO_END_1)
    link_end_2a = body.find(MACRO_END_2A)
    link_end_2b = body.find(MACRO_END_2B)
    link_end_3a = body.find(MACRO_END_3A)
    link_end_3b = body.find(MACRO_END_3B)
    if link_end_1 == -1 and \
        (link_end_2a == -1 and link_end_2b == -1) and \
        (link_end_3a == -1 and link_end_3b == -1):
        return None
    links_found = []
    body_length = len(body)
    # Append the found positions, changing them from -1 to
    # the length of the string if necessary, to make it
    # easier to work out which comes first.
    links_found.append(
        link_end_1 if link_end_1 != -1 else body_length
    )
    links_found.append(
        link_end_2a if link_end_2a != -1 else body_length
    )
    links_found.append(
        link_end_3a if link_end_3a != -1 else body_length
    )
    return links_found.index(min(links_found))+1
        

def search_for_link(buffer, body, first_search, server_uri, auth):
    """ Find the next user link in the body """
    link_start = body.find(MACRO_START)
    if link_start == -1:
        if (first_search):
            print("No user links found")
            return None
        # Copy over the remainder and exit
        buffer.write(body)
        return ""
    # Copy what leads up to that bit.
    buffer.write(body[:link_start])
    # Remove that from the body.
    body = body[link_start + len(MACRO_START):]
    # Work out which link type we've found
    link_type = which_link_type(body)
    if link_type is None:
        print("Cannot find end of user link")
        return None
    if link_type == 1:
        body, name, active_user = process_link_type_1(buffer, body, server_uri, auth)
    elif link_type == 2:
        body, name, active_user = process_link_type_2(buffer, body, server_uri, auth)
    elif link_type == 3:
        body, name, active_user = process_link_type_3(buffer, body, server_uri, auth)
    else:
        sys.exit("Unexpected link type %s" % link_type)
    # Substitute a plain text reference
    if not active_user:
        print("Replacing user link for %s" % name)
        buffer.write(name)
    return body

def check_for_user_links(original_body, server_uri, auth):
    """ Look for and replace any user links for ex-people """
    new_content = StringIO()
    first_search = True
    body = original_body
    while body != "":
        body = search_for_link(new_content, body, first_search, server_uri, auth)
        # Error or no links found
        if body is None:
            return None, False
        first_search = False
    new_body = new_content.getvalue()
    return new_body, new_body != original_body

def check_page(space, page_name, page_link, server_uri, auth):
    """ Check this page for any user links """
    print(page_name, page_link)
    result = requests.get("%s?expand=body.storage,version" % page_link, auth=auth)
    if result.status_code != 200:
        print("Cannot retrieve '%s'" % page_name)
        return
    data = result.json()
    new_body, result = check_for_user_links(data["body"]["storage"]["value"], server_uri, auth)
    if not result:
        print("No changes made")
        return
    current_version = data["version"]["number"]
    new_version = int(current_version) + 1
    data = {
        "id": data["id"],
        "type": "page",
        "title": data["title"],
        "body": {
            "storage": {
                "value": new_body,
                "representation": "storage"
            }
        },
        "version": {
            "number": new_version
        }
    }
    print("Updating page content")
    post_result = requests.put(page_link, auth=auth, json=data)
    if post_result.status_code != 200:
        print(post_result.text)
        sys.exit("Update failed")

load_config()
server_auth = get_auth("server_user", "server_pw")
page_types = get_pagetypes(CONFIG["server_uri"], server_auth, CONFIG["space_key"])
for type in page_types:
    pages = get_all_pages(CONFIG["server_uri"], server_auth, CONFIG["space_key"], type)
    #
    # Iterate through all of the pages to check them.
    for page in pages:
        check_page(CONFIG["space_key"], page, pages[page], CONFIG["server_uri"], server_auth)
