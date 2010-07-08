import csv
import git

from hashlib import sha512
from collections import defaultdict
from git import InvalidGitRepositoryError, NoSuchPathError
from netaddr import IPAddress, IPNetwork
from os import unlink
from socket import inet_ntoa, inet_aton
from struct import pack, unpack


try:
    import json
except ImportError:
    import simplejson as json

try:
    from itertools import product
except ImportError:
    def product(*args, **kwds):
        """List cartesian product - not available in Python 2.5"""
        pools = map(tuple, args) * kwds.get('repeat', 1)
        result = [[]]
        for pool in pools:
            result = [x+[y] for x in result for y in pool]
        for prod in result:
            yield tuple(prod)


protocols = ['IP','TCP', 'UDP', 'OSPF', 'IS-IS', 'SCTP', 'AH', 'ESP']

#files handling

class Table(list):
    """A list with pretty-print methods"""
    def __str__(self):
        cols = zip(*self)
        cols_sizes = [(max(map(len,i))) for i in cols] # get the widest entry for each column

        def j((n, li)):
            return "%d  " % n + "  ".join((item.ljust(pad) for item, pad in zip(li, cols_sizes) ))
        return '\n'.join(map(j, enumerate(self)))

    def len(self):
        return len(self)

# CSV files

def loadcsv(n, d='firewall'):
    try:
        f = open("%s/%s.csv" % (d, n))
        r = Table(csv.reader(f, delimiter=' '))
        f.close()
        return r
    except IOError:
        return [] #FIXME: why?

def savecsv(n, stuff, d='firewall'):
    f = open("%s/%s.csv" % (d, n), 'wb')
    writer = csv.writer(f,  delimiter=' ')
    writer.writerows(stuff)
    f.close()


# JSON files

def loadjson(n, d='firewall'):
    f = open("%s/%s.json" % (d, n))
    s = f.read()
    f.close()
    return json.loads(s)


def savejson(n, obj, d='firewall'):
    s = json.dumps(obj)
    f = open("%s/%s.json" % (d, n), 'wb')
    f.write(s)
    f.close()


# IP address parsing

def net_addr(a, n):
    q = IPNetwork('%s/%d' % (a, n)).network
    return str(q)

    addr = map(int, a.split('.'))
    x =unpack('!L',inet_aton(a))[0]  &  2L**(n + 1) -1
    return inet_ntoa(pack('L',x))


# Network objects

class NetworkObj(object):
    """Can be a host, a network or a hostgroup"""
    pass

class Sys(NetworkObj):
    def __init__(self, name, ifaces={}):
        self.ifaces = ifaces


class Host(NetworkObj):
    def __init__(self, name, iface, addr):
        self.name = name
        self.iface = iface
        self.ip_addr = addr


class Network(NetworkObj):
    def __init__(self, name, addr, masklen):
        self.name = name
        self.update(addr, masklen)

    def update(self, addr, masklen):
        """Get the correct network address and update attributes"""
        real_addr = net_addr(addr, masklen)
#        real_addr = long_to_dot(dot_to_long(addr) & masklen_to_long(masklen))
        self.ip_addr = real_addr
        self.netmasklen = masklen
        return real_addr, masklen, real_addr == addr

    def __contains__(self, other):
        """Check if a host or a network falls inside this network"""
        if isinstance(other, Host):
            return net_addr(other.ip_addr, self.netmasklen) == self.ip_addr

        elif isinstance(other, Network):
            addr_ok = net_addr(other.ip_addr, self.netmasklen) == self.ip_addr
            net_ok = other.netmasklen >= self.netmasklen
            return addr_ok and net_ok




class HostGroup(NetworkObj):
    """A Host Group contains hosts, networks, and other host groups"""

    def __init__(self, childs=[]):
        self.childs = childs

    def _flatten(self, i):
        if hasattr(i, 'childs'):
            return sum(map(self._flatten, i.childs), [])
        return [i]

    def networks(self):
        """Flatten the hostgroup and return its networks"""
        return [n for n in self._flatten(self) if isinstance(n, Network)]

    def hosts(self):
        """Flatten the hostgroup and return its hosts"""
        return filter(lambda i: type(i) == Host, self._flatten(self)) # better?
        return [n for n in self._flatten(self) if isinstance(n, Host)]


class FireSet(object):
    """A container for the network objects.
    Upon instancing the objects are loaded.
    """
    def __init__(self, repodir='firewall'):
        raise NotImplementedError

    # fireset management methods

    def save_needed(self):
        return True

    def save(self):
        pass

    def reset(self):
        pass

    def rollback(self, n):
        pass

    def version_list(self):
        return []

    # editing methods

    def delete(self, table, rid):
        assert table in ('rules', 'hosts', 'hostgroups', 'services', 'network') ,  "Incorrect table name."
        try:
            self.__dict__[table].pop(rid)
        except Exception, e:
            print "Unable to delete item %d in table %s: %s" % (rid, table, e)

    def rule_moveup(self, rid):
        try:
            rules[rid], rules[rid - 1] = rules[rid - 1], rules[rid]
        except Exception, e:
            #            say("Cannot move rule %d up." % rid)
            pass

    def rule_movedown(self, rid):
        try:
            rules[rid], rules[rid + 1] = rules[rid + 1], rules[rid]
        except Exception, e:
            #            say("Cannot move rule %d down." % rid)
            pass

    def rule_disable(self, rid):
        try:
            self.rules[rid][0] = 'n'
        except Exception, e:
            pass

    def rule_enable(self, rid):
        try:
            self.rules[rid][0] = 'y'
        except Exception, e:
            pass


    # deployment-related methods

    #
    # 1) The hostgroups are flattened, then the firewall rules are compiled into a big list of iptables commands.
    # 2) Firelet connects to the firewalls and fetch the iptables status and the existing interfaces (name, ip_addr, netmask)
    # 3) Based on this, the list is split in many sets - one for each firewall.

    #TODO: save the new configuration for each host and provide versioning.
    # Before deployment, compare the old (versioned), current (on the host) and new configuration for each firewall.
    # If current != versioned warn the user: someone made local changes.
    # Provide a diff of current VS new to the user before deploying.

    def _flattenhg(self, items, addr, net, hgs):
        """Flatten host groups tree, used in compile()"""
        def flatten1(item):
            li = addr.get(item), net.get(item), self._flattenhg(hgs.get(item), addr, net, hgs)  # should we convert network to string here?
            print repr(item)
            return filter(None, li)[0]
        if not items: return None
        return map(flatten1, items)


    def compile(self):
        """Compile iptables rules to be deployed in a single, big list. During the compilation many checks are performed."""

        assert not self.save_needed(), "Configuration must be saved before deployment."

        for rule in self.rules:
            assert rule[0] in ('y', 'n'), 'First field must be "y" or "n" in %s' % repr(rule)

        # build dictionaries to perform resolution
        addr = dict(((name + ":" + iface),ipa) for name,iface,ipa, is_m in self.hosts) # host to ip_addr
        net = dict((name, (n, mask)) for name, n, mask in self.networks) # network name
        hgs = dict((entry[0], (entry[1:])) for entry in self.hostgroups) # host groups
        hg_flat = dict((hg, self._flattenhg(hgs[hg], addr, net, hgs)) for hg in hgs) # flattened to {hg: hosts and networks}

        proto_port = dict((name, (proto, ports)) for name, proto, ports in self.services) # protocol
        proto_port['*'] = (None, '') # special case for "any"      # port format: "2:4,5:10,10:33,40,50"

        def res(n):
            if n in addr: return (addr[n], )
            elif n in net: return (net[n][0] + '/' + net[n][1], )
            elif n in hg_flat: return hg_flat[src][0][0]
            elif n == '*':
                return [None]
            else:
                raise Exception, "Host %s is not defined." % n

        compiled = []
        for ena, name, src, src_serv, dst, dst_serv, action, log_val, desc in self.rules:
            if ena == 'n':
                continue
            assert action in ('ACCEPT', 'DROP'),  'The Action field must be "ACCEPT" or "DROP" in rule "%s"' % name
            srcs = res(src)
            dsts = res(dst)
            sproto, sports = proto_port[src_serv]
            dproto, dports = proto_port[dst_serv]
            assert sproto in protocols + [None], "Unknown source protocol: %s" % sproto
            assert dproto in protocols + [None], "Unknown dest protocol: %s" % dproto

            if sproto and dproto and sproto != dproto:
                raise Exception, "Source and destination protocol must be the same in rule \"%s\"." % name
            if dproto:
                proto = " -p %s" % dproto.lower()
            elif sproto:
                proto = " -p %s" % sproto.lower()
            else:
                proto = ''

            if sports:
                ms = ' -m multiport' if ',' in sports else ''
                sports = "%s --sport %s" % (ms, sports)
            if dports:
                md = ' -m multiport' if ',' in dports else ''
                dports = "%s --dport %s" % (md, dports)

            # TODO: ensure that 'name' is a-zA-Z0-9_-

            try:
                log_val = int(log_val)  #TODO: try/except this
            except:
                raise Exception, "The logging field in rule \"%s\" must be an integer." % name

            for src, dst in product(srcs, dsts):
                src = " -s %s" % src if src else ''
                dst = " -d %s" % dst if dst else ''
                if log_val:
                    compiled.append("-A FORWARD%s%s%s%s%s --log-level %d --log-prefix %s -j LOG" %   (proto, src, sports, dst, dports, log_val, name))
                compiled.append("-A FORWARD%s%s%s%s%s -j %s" %   (proto, src, sports, dst, dports, action))

        return compiled

    def _get_confs(self):
        from flssh import get_confs
        self._remote_confs = None
        d = {}      # {hostname: [management ip address list ], ... }    If the list is empty we cannot reach that host.
        for n, iface, addr, is_m in self.hosts:
            if n not in d: d[n] = []
            if int(is_m):                            # IP address flagged for management
                d[n].append(addr)
        for n, x in d.iteritems():
            assert len(x), "No management IP address for %s " % n
        self._remote_confs = get_confs(d, username='root')

    def _check_ifaces(self):
        """Ensure that the interfaces configured on the hosts match the contents of the host table"""
        confs = self._remote_confs
        print '- ' * 30
        print repr(confs)
        for name,iface,ipa, is_m in self.hosts:
            if not name in confs:
                raise Exception, "Host %s not available." % name
            if not iface in confs[name][3]:
                raise Exception, "Interface %s missing on host %s" % (iface, name)
            ip_addr_v4, ip_addr_v6 = confs[name][3][iface]
#            print repr(confs[name][3][iface])
#            print name, iface, ipa
            assert ipa == ip_addr_v4.split('/')[0] or ipa == ip_addr_v6, "Wrong address on %s on interface %s" % (name, iface)

        #TODO: warn if there are extra interfaces?

#        for hostname, (session, ip_addr, iptables_save, ip_a_s) in self.confs:
#            for iface, (ip_addr_v4, ip_addr_v6) in ip_a_s:
#                pass



    def compile_dict(self, hosts=None, rset=None):
        """Generate set of rules specific for each host.
            rd = {hostname: {iface: [rules, ] }, ... }
        """
        assert not self.save_needed(), "Configuration must be saved before deployment."
        if not hosts: hosts = self.hosts
        if not rset: rset = self.compile()
        # r[hostname][interface] = [rule, rule, ... ]
        rd = defaultdict(dict)

        for hostname,iface,ipa, is_h in hosts:
            myrules = [ r for r in rset if ipa in r ]   #TODO: match subnets as well
            if not iface in rd[hostname]: rd[hostname][iface] = []
            rd[hostname][iface].extend(myrules)
        print repr(rd)
        return rd

    def deploy(self):
        """  """
        assert not self.save_needed(), "Configuration must be saved before deployment."
        # TODO: perform every step
        comp_rules = self.compile()
        self._get_confs
        self._check_ifaces
        self.rd = self.compile_dict()
        self._deliver_confs(self.rd)
        self._apply_remote_confs()

    def _deliver_confs(self):
        """Deliver the new iptables ruleset to each connected host"""
        #TODO: compare the actual and new ruleset, then deploy only the needed changes, then check?
        pass

    def _apply_remote_confs(self):
        pass #TODO


class DumbFireSet(FireSet):
    """Simple FireSet implementation without versioning. The changes are kept in memory."""

    def __init__(self, repodir='firewall'):
        self._repodir = repodir
        self.rules = loadcsv('rules', d=self._repodir)
        self.hosts = loadcsv('hosts', d=self._repodir)
        self.hostgroups = loadcsv('hostgroups', d=self._repodir)
        self.services = loadcsv('services', d=self._repodir)
        self.networks = loadcsv('networks', d=self._repodir)

#TODO: save_needed could become a bool attribute with getter and setter

    def _put_lock(self):
        open("%s/lock" % self._repodir, 'w').close()

    def save(self, msg):
        """Mem to disk"""
        if not self.save_needed(): return False  #TODO: handle commit message
        for table in ('rules', 'hosts', 'hostgroups', 'services', 'networks'):
            savecsv(table, self.__dict__[table], d=self._repodir)
        unlink("%s/lock" % self._repodir)
        return True

    def save_needed(self):
        try:
            open("%s/lock" % self._repodir, 'r').close()
            return True
        except:
            return False

    def reset(self):
        """Disk to mem"""
        if not self.save_needed(): return
        for table in ('rules', 'hosts', 'hostgroups', 'services', 'networks'):
            self.__dict__[table] = loadcsv(table, d=self._repodir)
        unlink("%s/lock" % self._repodir)

    def delete(self, table, rid):
        assert table in ('rules', 'hosts', 'hostgroups', 'services', 'networks') ,  "TODO"
        try:
            self.__dict__[table].pop(rid)
            self._put_lock()
        except Exception, e:
            pass #TODO

    def rule_moveup(self, rid):
        try:
            rules = self.rules
            rules[rid], rules[rid - 1] = rules[rid - 1], rules[rid]
            self.rules = rules
            self._put_lock()
        except Exception, e:
            print e
            #            say("Cannot move rule %d up." % rid)

    def rule_movedown(self, rid):
        try:
            rules = self.rules
            rules[rid], rules[rid + 1] = rules[rid + 1], rules[rid]
            self.rules = rules[:]
            self._put_lock()
        except Exception, e:
            #            say("Cannot move rule %d down." % rid)
            pass

    def rollback(self, n):
        pass

    def version_list(self):
        return (('timestamp', 'version id','author','changelog'), )


class GitFireSet(FireSet):
    """FireSet implementing Git to manage the configuration repository"""

    def __init__(self, repodir='firewall'):
        self.rules = loadcsv('rules')
        self.hosts = loadcsv('hosts')
        self.hostgroups = loadcsv('hostgroups')
        self.services = loadcsv('services')
        self.networks = loadcsv('networks')

        try:
            self._repo = git.Repo(repodir) #TODO full path
        except InvalidGitRepositoryError:
            self._repo = git.Repo.create(repodir, mkdir=True)
        except NoSuchPathError:
            self._repo = git.Repo.create(repodir, mkdir=True)

    def version_list(self):
        return self._repo.commits(self, max_count=30)

    def save_needed(self):
        return self._repo.is_dirty






# Firewall ruleset processing












"""
*raw
:PREROUTING ACCEPT
:OUTPUT ACCEPT
COMMIT

*mangle
:PREROUTING ACCEPT
:INPUT ACCEPT
:FORWARD ACCEPT
:OUTPUT ACCEPT
:POSTROUTING ACCEPT
COMMIT

*nat
:PREROUTING ACCEPT
:POSTROUTING ACCEPT
:OUTPUT ACCEPT
COMMIT

*filter
:INPUT ACCEPT
:FORWARD ACCEPT
:OUTPUT ACCEPT
-A INPUT -s 4.4.4.4/32 -p tcp -m multiport --sports 0:65535 -m multiport --dports 2:4,5:10,10:33 -j ACCEPT
-A INPUT -s 4.4.4.4/32 -p tcp -m multiport --sports 0:65535 -m multiport --dports 2:4,5:10,10:33 -j ACCEPT
-A INPUT -s 4.4.4.4/32 -p tcp -m tcp --dport 2:4 -j ACCEPT
-A INPUT -s 3.3.3.3/32 -j ACCEPT
-A INPUT -s 3.3.3.0/30 -j ACCEPT
-A INPUT -s 3.3.3.3/32 -j ACCEPT
COMMIT
"""




# #  User management  # #

#TODO: add creation and last access date?

class Users(object):
    """User management, with password hashing.
    users = {'username': ['role','pwdhash','email'], ... }
    """

    def __init__(self, d=''):
        self._dir = d
        try:
            self._users = loadjson('users', d=d)
        except:
            self._users = {} #TODO: raise alert?

    def _save(self):
        savejson('users', self._users, d=self._dir)

    def _hash(self, u, pwd): #TODO: should I add salting?
        return sha512("%s:::%s" % (u, pwd)).hexdigest()

    def create(self, username, role, pwd, email=None):
        assert username, "Username must be provided."
        assert username not in self._users, "User already exists."
        self._users[username] = [role, self._hash(username, pwd), email]
        self._save()

    def update(self, username, role=None, pwd=None, email=None):
        assert username in self._users, "Non existing user."
        if role is not None:
            self._users[username][0] = role
        if pwd is not None:
            self._users[username][1] = self._hash(username, pwd)
        if email is not None:
            self._users[username][2] = email
        self._save()

    def delete(self, username):
        try:
            self._users.pop(username)
        except KeyError:
            raise Exception, "Non existing user."
        self._save()

    def validate(self, username, pwd):
        assert username, "Missing username."
        assert username in self._users, "Non existing user."
        assert self._hash(username, pwd) == self._users[username][1], "Incorrect password."
        #TODO: should I return True?














