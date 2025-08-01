#!/usr/bin/env python
# License: GPLv3 Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>


import json

from calibre import as_unicode
from calibre.srv.errors import HTTPBadRequest, HTTPForbidden
from calibre.srv.routes import endpoint
from calibre.srv.users import validate_password
from calibre.utils.localization import _


@endpoint('/users/change-pw', methods={'POST'})
def change_pw(ctx, rd):
    user = rd.username or None
    if user is None:
        raise HTTPForbidden('Anonymous users are not allowed to change passwords')
    if not ctx.user_manager.is_allowed_to_change_password_via_http(user):
        raise HTTPForbidden(f'The user {user} is not allowed to change passwords')
    try:
        pw = json.loads(rd.request_body_file.read())
        oldpw, newpw = pw['oldpw'], pw['newpw']
    except Exception:
        raise HTTPBadRequest('No decodable password found')
    if oldpw != ctx.user_manager.get(user):
        raise HTTPBadRequest(_('Existing password is incorrect'))
    err = validate_password(newpw)
    if err:
        raise HTTPBadRequest(err)
    try:
        ctx.user_manager.change_password(user, newpw)
    except Exception as err:
        raise HTTPBadRequest(as_unicode(err))
    ctx.log.warn('Changed password for user', user)
    return f'password for {user} changed'
