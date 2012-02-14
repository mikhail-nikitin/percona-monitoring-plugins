.. _nagios_overview:

Percona Monitoring Plugins for Nagios
=====================================

Many of the freely available Nagios plugins for MySQL are poor quality, with no
formal testing and without good documentation.  A more serious problem, however,
is that they are not created by experts in MySQL monitoring, so they tend to
cause false alarms and noise, and don't encourage good practices to monitor what
matters.

These plugins offer the following improvements:

* Created by MySQL experts.
* Good documentation.
* Support for the newest versions of MySQL and InnoDB.
* Integration with other Percona software, such as Percona Server and Percona Toolkit.
* Easy to install and configure.
* Real software engineering!  There is a test suite, to keep the code high quality.

The plugins are designed to be executed locally or via NRPE.  Most large
installations should probably use NRPE for security and scalability.

In general, the plugins either examine the local UNIX system and execute
commands, or they connect to MySQL via the ``mysql`` commandline executable and
retrieve information. Some plugins combine these actions.  Each plugin's
documentation explains its commandline options and arguments, as well as the
commands executed and the privileges required.

System Requirements
===================

The plugins are all written in standard Unix shell script. They should run on
any Unix or Unix-like operating system, such as GNU/Linux, Solaris, or FreeBSD.

The plugins are designed to be used with MySQL 5.0 and newer versions, but they
may work on 4.1 or older versions as well.

Configuration Best Practices
============================

These plugins can be used locally or via NRPE.  NRPE is the suggested
configuration.  Some plugins execute commands that require privileges, so you
may need to specify a command prefix to execute them with ``sudo``.

List of Plugins
===============

.. toctree::
   :maxdepth: 1
   :glob:

   support
   pmp-*