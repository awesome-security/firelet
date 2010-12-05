# Firelet - Distributed firewall management.
# Copyright (C) 2010 Federico Ceratto
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from datetime import datetime
from pxssh import pxssh, TIMEOUT, EOF
from threading import Thread

from flutils import Bunch

import logging
log = logging.getLogger()

def _exec(c, s):
    """Execute remote command"""
    c.sendline(s)
    c.prompt()
    ret = c.before.split('\n')
    return map(str.rstrip, ret)


class SSHConnector(object):
    """Manage a pool of pxssh connections to the firewalls. Get the running
    configuation and deploy new configurations.
    """
    def __init__(self, targets=None, username='firelet'):
        self._pool = {} # connections pool: {'hostname': pxssh session, ... }
        self._targets = targets   # {hostname: [management ip address list ], ... }
        assert isinstance(targets, dict), "targets must be a dict"
        self._username = username

    def get_conf(self, confs, hostname, ip_addr, username):
        """Connect to a firewall and get its configuration.
            Save the output in a shared dict d"""
        c = pxssh(timeout=5000)
        try:
            c.login(ip_addr, username)
        except (TIMEOUT, EOF):
            c.close()
            if c.isalive():
                c.close(force=True)
            return
        log.debug("Connected to %s" % hostname)
        iptables_save = _exec(c,'sudo /sbin/iptables-save')
        ip_addr_show = _exec(c, '/bin/ip addr show')

        c.close()
        if c.isalive():
            c.close(force=True)

        iptables_p = self.parse_iptables_save(iptables_save)
        ip_a_s_p = self.parse_ip_addr_show(ip_addr_show)
        d = Bunch(iptables=iptables_p, ip_a_s=ip_a_s_p)
        confs[hostname] = d

    def get_confs(self, keep_sessions=False):
        """Connects to the firewalls, get the configuration and return:
            { hostname: Bunch of "session, ip_addr, iptables-save, interfaces", ... }
        """
        confs = {}
        threads = []
        for hostname, ip_addrs in self._targets.iteritems():
            confs[hostname] = None
            t = Thread(target=self.get_conf, args=(confs, hostname,
                ip_addrs[0], 'firelet'))
            threads.append(t)
            t.start()

        map(Thread.join, threads)

        return confs


    def parse_iptables_save(self, s):
        """Parse iptables-save output and returns a dict:
        {'filter': [rule, rule, ... ], 'nat': [] }
        """

        def start(li, tag):
            for n, item in enumerate(li):
                if item == tag:
                    return li[n:]
            return []

        def get_block(li, tag):
            li = start(li, tag)
            for n, item in enumerate(li):
                if item == 'COMMIT':
                    return li[:n]
            return []

        def good(x):
            return x.startswith(('-A PREROUTING', '-A POSTROUTING',
                '-A OUTPUT', '-A INPUT', '-A FORWARD'))


        block = get_block(s, '*nat')
        b = filter(good, block)
        nat = '\n'.join(b)
    #    for q in ('PREROUTING', 'POSTROUTING', 'OUTPUT'):
    #        i['nat'][q] = '\n'.join(x for x in block if x.startswith('-A %s' % q))

        block = get_block(s, '*filter')
        b = filter(good, block)

        return Bunch(nat=nat, filter=b)

    #    for q in ('INPUT', 'OUTPUT', 'FORWARD'):
    #        i['filter'][q] = '\n'.join(x for x in block if x.startswith('-A %s' % q))

        return i


    def parse_ip_addr_show(self, s):
        """Parse the output of 'ip addr show' and returns a dict:
        {'iface': (ip_addr_v4, ip_addr_v6)} """
        iface = ip_addr_v4 = ip_addr_v6 = None
        d = {}
        for q in s[1:]:
            if q and not q.startswith('  '):   # new interface definition
                if iface:
                    d[iface] = (ip_addr_v4, ip_addr_v6) # save previous iface, if existing
                iface = q.split()[1][:-1]  # second field, without trailing column
                ip_addr_v4 = ip_addr_v6 = None
            elif q.startswith('    inet '):
                ip_addr_v4 = q.split()[1]
            elif q.startswith('    inet6 '):
                ip_addr_v6 = q.split()[1]
        if iface:
            d[iface] = (ip_addr_v4, ip_addr_v6)
        return d


    def deliver_conf(self, status, hostname, ip_addr, username, conf):
        """Connect to a firewall and deliver iptables configuration.
            """
        c = pxssh(timeout=5000)
        try:
            c.login(ip_addr, username)
        except (TIMEOUT, EOF):
            c.close()
            if c.isalive():
                c.close(force=True)
            return

        log.debug("Connected to %s" % hostname)

#        tstamp = datetime.utcnow().isoformat()[:19]
#
#        c.sendline("cat > .iptables-%s << EOF" % tstamp)
        for x in block:
            c.sendline(x)
        c.sendline('EOF')
        c.prompt()
        ret = c.before
        log.debug("Deployed ruleset file to %s, got %s" % (hostname, ret)  )

        c.close()
        if c.isalive():
            c.close(force=True)

        status[hostname] = 'ok'



    def deliver_confs(self, newconfs_d):
        """Connects to the firewall, deliver the configuration.
            hosts_d = { host: [session, ip_addr, iptables-save, interfaces], ... }
            newconfs_d =  {hostname: {iface: [rules, ] }, ... }
        """
        assert isinstance(newconfs_d, dict), "Dict expected"

        status = {}
        threads = []
        for hostname, ip_addrs in self._targets.iteritems():
            status[hostname] = None
            block = ["# Created by Firelet for host %s" % hostname,
                '*filter']
            for rules in newconfs_d[hostname].itervalues():
                for rule in rules:
                    block.append(str(rule))
            block.append('COMMIT')
            block.append('EOF')
            t = Thread(target=self.deliver_conf, args=(status, hostname,
                ip_addrs[0], 'firelet', block ))
            threads.append(t)
            t.start()
            print 'st'

        map(Thread.join, threads)
        assert False,  repr(status)
#iptables_save = _exec(c,'sudo /sbin/iptables-save')
#        for hostname, p in self._pool.iteritems():
#            p.sendline('cat > /tmp/newiptables << EOF')
#            p.sendline('# Created by Firelet for host %s' % hostname)
#            p.sendline('*filter')
#            for iface, rules in newconfs_d[hostname].iteritems():
#                [ p.sendline(str(rule)) for rule in rules ]
#            p.sendline('COMMIT')
#            p.sendline('EOF')
#            p.prompt()
#            ret = p.before
#            log.debug("Deployed ruleset file to %s, got %s" % (hostname, ret)  )
#        return


    def apply_remote_confs(self, keep_sessions=False):
        """Loads the deployed ruleset on the firewalls"""
        self._connect()

        for hostname, p in self._pool.iteritems():
            ret = self._interact(p,'/sbin/iptables-restore < /tmp/newiptables')
            log.debug("Deployed ruleset file to %s, got %s" % (hostname, ret)  )

        if not keep_sessions: self._disconnect()
        return

    def _disconnect(self, *a):
        pass



class MockSSHConnector(SSHConnector):
    """Used in Demo mode and during unit testing to prevent network interactions.
    Only some methods from SSHConnector are redefined.
    """


    def get_confs(self, keep_sessions=False):
        """Connects to the firewalls, get the configuration and return:
            { hostname: Bunch of "session, ip_addr, iptables-save, interfaces", ... }
        """
        bad = self._connect()
        assert len(bad) < 1, "Cannot connect to a host:" + repr(bad)
        confs = {} # {hostname:  Bunch(), ... }

        for hostname, p in self._pool.iteritems():
            iptables = self._interact(p, 'sudo /sbin/iptables-save')
            iptables_p = self.parse_iptables_save(iptables)
            ip_a_s = self._interact(p,'/bin/ip addr show')
            ip_a_s_p = self.parse_ip_addr_show(ip_a_s)
            confs[hostname] = Bunch(iptables=iptables, ip_a_s=ip_a_s_p)
        if not keep_sessions:
            log.debug("Closing connections.")
            d = self._disconnect()
#        log.debug("Dictionary built by get_confs: %s" % repr(confs))
        return confs





    def _connect(self):
        """Connects to the firewalls on a per-need basis.
        Returns a list of unreachable hosts.
        """
        unreachables = []
        for hostname, addrs in self._targets.iteritems():
            if hostname in self._pool and self._pool[hostname]:
                continue # already connected
            assert len(addrs), "No management IP address for %s, " % hostname
            ip_addr = addrs[0]      #TODO: cycle through different addrs?
            p = hostname # Instead of a pxssh session, the hostname is stored here
            self._pool[hostname] = p
        return unreachables

    def _disconnect(self):
        """Disconnects from the hosts and purge the session from the dict"""
        for hostname, p in self._pool.iteritems():
            try:
#                p.logout()
                self._pool[hostname] = None
            except:
                log.debug('Unable to disconnect from host "%s"' % hostname)
        #TODO: delete "None" hosts

    def _interact(self, p, s):
        """Fake interaction using files instead of SSH connections"""
        d = self.repodir
        if s == 'sudo /sbin/iptables-save':
            log.debug("Reading from %s/iptables-save-%s" % (d, p))
            return map(str.rstrip, open('%s/iptables-save-%s' % (d, p)))
        elif s == '/bin/ip addr show':
            log.debug("Reading from %s/ip-addr-show-%s" % (d, p))
            return map(str.rstrip, open('%s/ip-addr-show-%s' % (d, p)))
        else:
            raise NotImplementedError

    def deliver_confs(self, newconfs_d):
        """Write the conf on local temp files instead of delivering it.
            newconfs_d =  {hostname: [iptables-save line, line, line, ], ... }
        """
        assert isinstance(newconfs_d, dict), "Dict expected"
        self._connect()
        d = self.repodir
        for hostname, p in self._pool.iteritems():
            li = newconfs_d[hostname]
            log.debug("Writing to %s/iptables-save-%s and -x" % (d, p))
            open('%s/iptables-save-%s' % (d, p), 'w').write('\n'.join(li)+'\n')
            open('%s/iptables-save-%s-x' % (d, p), 'w').write('\n'.join(li)+'\n')
            ret = ''
            log.debug("Deployed ruleset file to %s, got %s" % (hostname, ret)  )
        return
        #TODO: fix deliver_confs in SSHConnector

    def apply_remote_confs(self, keep_sessions=False):
        """Loads the deployed ruleset on the firewalls"""
        self._connect()
        # No way to test the iptables-restore.
        if not keep_sessions: self._disconnect()
        return





