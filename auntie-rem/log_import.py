#!/bin/python
# -*- coding: utf-8 -*-

import re
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, String, Sequence, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, backref
from lxml.html import parse as html_parse
import requests

engine = create_engine('postgresql://auntie:auntie-password@localhost:5432/auntie-rem')
Session = sessionmaker(bind = engine)
session = Session()
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, Sequence('user_id_seq'), primary_key = True)
    nick = Column(String)

    def __repr__(self):
       return '<user %s>' % self.nick

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
Message.in_response_to = relationship(Message, backref='messages',
    remote_side = Message.id)

Base.metadata.create_all(engine)

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

def populate():
    logs = html_parse('http://tunes.org/~nef/logs/lisp/')
    archives = logs.xpath('//td/a/text()')
    for arch in archives:
        if re.match(r'\d\d\.\d\d\.\d\d', arch):
            print 'will import http://tunes.org/~nef/logs/lisp/%s' % arch
            log_file = requests.get('http://tunes.org/~nef/logs/lisp/%s' % arch)
            date = [int(x) for x in arch.split('.')]
            session.add_all(parse_message(date, line)
                            for line in clean_lines(log_file.content))
    session.commit()
                

if __name__ == '__main__':
    populate()
