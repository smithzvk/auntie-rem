#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import os
import requests
import logging

from string import punctuation
from datetime import datetime
from sqlalchemy import create_engine, Table, Column, Integer, String, \
    Sequence, ForeignKey, DateTime, and_
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
    conversation_id = Column(Integer, ForeignKey('conversations.id'))
    
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

word_index = Table('word_association', Base.metadata,
    Column('index_id', Integer, ForeignKey('index.id')),
    Column('conversation_id', Integer, ForeignKey('conversations.id')))

class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(Integer, Sequence('conversation_id_seq'), primary_key = True)
    messages = relationship('Message', order_by = 'Message.id',
                            backref = 'conversations')

class Index(Base):
    __tablename__ = 'index'
    id = Column(Integer, Sequence('word_id_seq'), primary_key = True)
    word = Column(String, nullable = False)
    conversations = relationship('Conversation',
                                 order_by = 'Conversation.id',
                                 secondary = word_index)

class MessageParser(object):
    stopwords = None
    script_home = os.path.realpath(os.path.dirname(__file__))

    def __init__(self, state):
        self.state = state
        self.splitter = re.compile(r'[\s{}]+'.format(re.escape(punctuation)))
        self.cmd = re.compile(r'((\d\d):(\d\d):(\d\d)) --- (join|quit): ([^ ]+) (.*)')
        self.msg = re.compile(r'((\d\d):(\d\d):(\d\d))( )<([^)>]+)> (.*)')

        if not MessageParser.stopwords:
            with open(os.path.join(
                    MessageParser.script_home, 'stopwords.txt'), 'r') as f:
                MessageParser.stopwords = {(x, True) for x in f}
    
    def parse_message(self, date, line):
        # 00:00:00 --- log: started lisp/15.01.01
        # 00:06:42 <pjb> Cons Ignucius.
        result = Message()
        match = re.match(self.cmd, line)
        is_message = not match

        if is_message: match = re.match(self.msg, line)
        if match:
            result.ts = datetime(*(date + [int(x) for x in match.group(2, 3, 4)]))
            result.user = self.state.find_or_create_user(match.group(6))
            try:
                result.text = match.group(7)
            except:
                result.text = ''
            logging.debug('Added message: "%s"' % result.text)
        if result.text:
            self.state.messages.append(result)
        if not is_message:
            if match.group(5) == 'quit':
                self.state.forget_user(result.user)
        if is_message and result.text:
            for unanswered in self.state.unanswered_messages:
                if result.user.nick in unanswered.text:
                    result.in_response_to = unanswered
                    self.state.unanswered_messages.remove(unanswered)
                    break
            convo = self.state.conversation_for_message(result)
            if not convo:
                convo = self.state.open_conversation(result)
            else: self.state.say(result, convo)
            self.index_words(result, convo)

    def index_words(self, message, convo):
        for word in re.split(self.splitter, message.text):
            if not word in MessageParser.stopwords:
                index = self.state.index.get(word, None)
                if not index: index = Index()
                index.word = word
                convos = index.conversations or []
                convos.append(convo)
                index.conversations = convos
                self.state.index[word] = index

    def clean_lines(self, content):
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

    def parse(self, archives, options):
        done = 0
        for arch in archives:
            if re.match(r'\d\d\.\d\d\.\d\d', arch):
                if options.max > -1 and done >= options.max:
                    break
                logging.info('%d of %d importing http://tunes.org/~nef/logs/lisp/%s' % \
                             (done, options.max, arch))
                log_file = requests.get('http://tunes.org/~nef/logs/lisp/%s' % arch)
                date = [int(x) for x in arch.split('.')]
                for line in self.clean_lines(log_file.content):
                    self.parse_message(date, line)
                done += 1

class ChatState(object):

    def __init__(self):
        self.users_online = set()
        self.active_conversations = []
        self.index = {}
        self.messages = []
        self.parser = MessageParser(self)
        self.unanswered_messages = []

    def conversation_for_message(self, message):
        # TODO: This needs improvement to recognize conversations.
        for convo in self.active_conversations:
            for old_message in convo.messages:
                if message.in_response_to == old_message \
                   or old_message.user == message.user \
                   or (message.user and message.user.nick in old_message.text):
                    return convo
        return None
        
    def open_conversation(self, message):
        convo = Conversation()
        convo.messages = [message]
        self.unanswered_messages.append(message)
        self.users_online.add(message.user)
        self.active_conversations.append(convo)
        return convo

    def close_conversation(self, convo):
        self.active_conversations.remove(convo)
        session.add(convo)
        for message in convo.messages:
            try:
                self.unanswered_messages.remove(message)
            except:
                pass

    def forget_user(self, user):
        try:
            self.users_online.remove(user)
        except:
            pass
        for convo in self.active_conversations:
            should_remove = True
            for message in convo.messages:
                if message.user in self.users_online:
                    should_remove = False
                    break
            if should_remove:
                self.close_conversation(convo)

    def say(self, message, convo):
        self.users_online.add(message.user)
        self.unanswered_messages.append(message)
        convo.messages.append(message)

    def word_index(self):
        return self.index.values()

    def find_or_create_user(self, nick):
        user = session.query(User).filter_by(nick = nick).first()
        if not user:
            user = User()
            logging.info('Created user: %s' % nick)
            user.nick = nick
            session.add(user)
        return user

    def objects(self):
        return self.messages + self.active_conversations + self.word_index()

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
    archives = html_parse(options.url).xpath('//td/a/text()')
    state = ChatState()
    state.parser.parse(archives, options)
    session.add_all(state.objects())
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
                        'These options manage connection to the database.')
    group.add_option('-p', '--port', dest = 'port', default = 5432, type = 'int',
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
    group = OptionGroup(parser, 'Import options',
                        'These options manage information import.')
    group.add_option(
        '-l', '--logs-url', dest = 'url', default = 'http://tunes.org/~nef/logs/lisp/',
        help = 'URL to fetch the logs from (this option is incompatible with --test)')
    group.add_option(
        '-m', '--max', dest = 'max', default = -1, type = 'int',
        help = 'Parse at most this many logs (negative values mean no restriction)')
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
