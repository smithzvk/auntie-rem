#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import requests
import logging

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Sequence, ForeignKey, DateTime, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, backref
from lxml.html import parse as html_parse
from optparse import OptionParser, OptionGroup
from IPython import embed;

session = None
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, Sequence('user_id_seq'), primary_key = True)
    nick = Column(String)

    def __repr__(self):
       return '<user %s>' % self.nick

    def is_online_at(self, ts):
        previous = session.query(Message).filter_by(
            and_(Message.nick == self.nick, Message.ts < ts)).\
            order_by(Message.ts.asc()).first()
        return not re.match(r'quit', previous.command or '')
                                             

class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, Sequence('message_id_seq'), primary_key = True)
    text = Column(String)
    ts = Column(DateTime)
    # This could be an enum, but we don't really care about system messages
    command = Column(String)
    user_id = Column(Integer, ForeignKey('users.id'))
    user = relationship('User', backref = backref('messages', order_by = id))
    
    def __repr__(self):
        if self.command:
            return '%s --- %s %s %s' % \
                (self.ts, self.command, self.user.nick, self.text)
        else:
            return '%s <%s> %s' % \
                (self.ts, self.user.nick, self.text)

# References to self aren't possible from inside the class definition...
Message.cause_id = Column(Integer, ForeignKey(Message.id))
Message.in_response_to = relationship(Message, backref = 'messages',
    remote_side = Message.id)



def find_or_create_user(nick):
    user = session.query(User).filter_by(nick = nick).first()
    if not user:
        user = User()
        user.nick = nick
    return user

def parse_message(date, line):
    # 00:00:00 --- log: started lisp/15.01.01
    # 00:06:42 <pjb> Cons Ignucius.
    result = Message()
    cmd = r'((\d\d):(\d\d):(\d\d)) --- ([^:]+): (.*)'
    msg = r'((\d\d):(\d\d):(\d\d)) <([^)]+)> (.*)'
    match = re.match(cmd, line)
    if not match: match = re.match(msg, line)
    if match:
        result.ts = datetime(*(date + [int(x) for x in match.group(2, 3, 4)]))
        result.user = find_or_create_user(match.group(5))
        result.text = match.group(6)
    return result

def clean_lines(content):
    line, begin, end = None, 0, 0
    for byte in content:
        end += 1
        if byte == '\n':
            try:
                line = content[begin : end].decode('ascii')
            except UnicodeDecodeError:
                try:
                    line = content[begin : end].decode('utf-8')
                except UnicodeDecodeError:
                    begin = end
                    continue
            begin = end
            yield line

def start_engine(options):
    global session
    connection = 'postgresql://%s:%s@%s:%s/%s' % \
                 (options.user, options.password, options.host,
                  options.port, options.database)
    logging.info('Connecting with: \'%s\'' % connection)
    engine = create_engine(connection)
    Session = sessionmaker(bind = engine)
    session = Session()
    Base.metadata.create_all(engine)

def populate(options):
    start_engine(options)
    logs = html_parse(options.url)
    archives = logs.xpath('//td/a/text()')
    for arch in archives:
        if re.match(r'\d\d\.\d\d\.\d\d', arch):
            
            print 'will import http://tunes.org/~nef/logs/lisp/%s' % arch
            log_file = requests.get('http://tunes.org/~nef/logs/lisp/%s' % arch)
            date = [int(x) for x in arch.split('.')]
            session.add_all(parse_message(date, line)
                            for line in clean_lines(log_file.content))
    session.commit()
                
def test(options):
    start_engine(options)
    embed()
    
if __name__ == '__main__':
    parser = OptionParser(usage = 'Usage: split-swc.py [options] <swc, swf or xml>')
    'postgresql://auntie:auntie-password@localhost:5432/auntie-rem'
    def check_format(option, opt_str, value, parser, *args, **kwargs):
        if not value in ['swf', 'swc']:
            parser.error('Supported formats are swf and swc.')
    group = OptionGroup(parser, 'Database options',
                        'These optins manage connection to the database.')
    group.add_option('-p', '--port', dest = 'port', default = 5432,
                     help = 'Port where Postgresql server listens.')
    group.add_option('-u', '--user', dest = 'user', default = 'auntie',
                     help = 'Postgresql user.')
    group.add_option('-d', '--database', dest = 'database', default = 'auntie-rem',
                     help = 'Postgresql database to connect to.')
    group.add_option('-s', '--server-host', dest = 'host', default = 'localhost',
                     help = 'Host running the Postgresql server.')
    group.add_option('-w', '--password', dest = 'password', default = 'auntie-password',
                     help = 'Postgresql connection password.')
    parser.add_option_group(group)
    parser.add_option(
        '-l', '--logs-url', dest = 'url', default = 'http://tunes.org/~nef/logs/lisp/',
        help = 'URL to fetch the logs from (this option is incompatible with --test)')
    parser.add_option(
        '-t', '--test', dest = 'test', action = 'store_true', default = False,
        help = 'Instruct the script to start an interactive session.')
    parser.add_option(
        '-v', '--verbose', dest = 'verbose', action = 'count',
        help = 'Increase verbosity (specify multiple times for more)')
    options, args = parser.parse_args()

    log_level = logging.WARNING
    if options.verbose == 1:
        log_level = logging.INFO
    elif options.verbose >= 2:
        log_level = logging.DEBUG

    logging.basicConfig(level = log_level)

    if options.test: test(options)
    else: populate(options)
