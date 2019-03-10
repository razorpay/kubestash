import argparse
import base64
import sys
import time
import urllib3
import ssl
import os
import kubernetes
import credstash
import boto3
import copy
from collections import namedtuple


# TODO: args.profile, args.arn
# TODO: args.version


def base_parser():
    """ Parses arguments shared by every subcommand. """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('-p', '--proxy',
                        action='store',
                        type=str,
                        nargs=1,
                        help='hostname of a kubernetes apiserver to use, for example: --proxy 127.0.0.1:8080')
    parser.add_argument('-v', '--verbose',
                        dest='verbose',
                        action='store_true',
                        help='verbose output')
    parser.add_argument('-in-cluster', '--in-cluster',
                        dest='in_cluster',
                        action='store_true',
                        help='In Cluster Mode')
    parser.add_argument('--trace',
                        dest='trace',
                        action='store_true',
                        help='show the full stack trace when an SSLError happens')
    parser.add_argument('-f', '--force',
                        dest='force',
                        action='store_true',
                        help='replace a secret if it already exists')
    parser.add_argument('-n', '--namespace',
                        dest='namespace',
                        action='store',
                        type=str,
                        default='default',
                        help='kubernetes namespace')
    parser.add_argument('-e', '--environment',
                        dest='environment',
                        action='store',
                        type=str,
                        default=None,
                        help='kubernetes cluster(stage/prod)')
    parser.add_argument('-l', '--lowercase',
                        dest='lowercase',
                        action='store_true',
                        help='For SECRET keys, lowercase and convert "_" to "-" (DNS_SUBDOMAIN). '
                             'Useful for compatibility with older Kubernetes versions. '
                             '(DEPRECATED).')
    parser.add_argument('-U', '--uppercase',
                        dest='uppercase',
                        action='store_true',
                        help='For environment variable keys, uppercase and convert dashes to undescores.'
                        'Useful if your keys in credstash are in lowercase')
    return parser


def add_parser_inject(parent):
    """ Parses arguments for the inject command. """
    parser = parent.add_parser('inject',
                               parents=[base_parser()],
                               help='inject env variables into a Kubernetes deployment manifest, '
                                    'taken from a Kubernetes secret (DEPRECATED; see README.md, use envFrom instead)')
    parser.add_argument('secret',
                        action='store',
                        type=str,
                        help='Kubernetes secret you want to take values from')
    parser.add_argument('deployment',
                        action='store',
                        type=str,
                        help='Kubernetes deployment to inject env values into')
    parser.add_argument('-c', '--container',
                        action='append',
                        default=[],
                        type=str,
                        help='specify one or more containers to insert env values into (default is all containers)')
    parser.add_argument('-u', '--update-only',
                        dest='update_only',
                        action='store_true',
                        help='only update envs that are already present (do not append), useful for updating envs to '
                             'point to a different secret')
    return parser


def add_parser_push(parent):
    """ Parses arguments for the push command. """
    parser = parent.add_parser('push',
                               parents=[base_parser()],
                               help='push values from a Credstash table to a Kubernetes secret')
    parser.add_argument('table',
                        action='store',
                        type=str,
                        help='Credstash table you want to pull values from')
    parser.add_argument('secret',
                        action='store',
                        type=str,
                        help='Kubernetes secret you want to push values in')
    parser.add_argument('-c', '--context',
                        dest='context',
                        action='store',
                        type=str,
                        default=None,
                        help='kubernetes context (ignored if proxy is set)')
    parser.add_argument('-r', '--region',
                        dest='region',
                        action='store',
                        type=str,
                        default=None,
                        help='aws region')
    return parser


def add_parser_pushall(parent):
    """ Parses arguments for the pushall command. """
    parser = parent.add_parser('pushall',
                               parents=[base_parser()],
                               help='push values from a Credstash table to a Kubernetes cluster')
    parser.add_argument('table',
                        action='store',
                        type=str,
                        help='Credstash table you want to pull values from')

    parser.add_argument('--secretname',
                        action='store',
                        type=str,
                        help='ENV_NAME you want to sync (requires --secret and --namespace)')

    parser.add_argument('--secret',
                        action='store',
                        type=str,
                        help='Kubernetes secret you want to push values in')
    parser.add_argument('-c', '--context',
                        dest='context',
                        action='store',
                        type=str,
                        default=None,
                        help='kubernetes context (ignored if proxy is set)')
    parser.add_argument('-r', '--region',
                        dest='region',
                        action='store',
                        type=str,
                        default=None,
                        help='aws region')
    return parser


def add_parser_daemon(parent):
    """ Parses arguments for the daemon command. """
    parser = parent.add_parser('daemon',
                               parents=[base_parser()],
                               help='daemon mode; automatically runs a `kubestash push` whenever changes are '
                                    'detected in DynamoDB. Requires DynamoDB Streams to be enabled for your table. '
                                    'Implies -f --force.')
    parser.add_argument('table',
                        action='store',
                        type=str,
                        help='Credstash table you want to pull values from')
    parser.add_argument('secret',
                        action='store',
                        type=str,
                        help='Kubernetes secret you want to push values in')
    parser.add_argument('-c', '--context',
                        dest='context',
                        action='store',
                        type=str,
                        default=None,
                        help='kubernetes context (ignored if proxy is set)')
    parser.add_argument('-r', '--region',
                        dest='region',
                        action='store',
                        type=str,
                        default=None,
                        help='aws region')
    parser.add_argument('-i', '--interval',
                        dest='interval',
                        action='store',
                        type=int,
                        default=10,
                        help='how long to sleep between shard iterations (seconds)')
    return parser


def add_parser_daemonall(parent):
    """ Parses arguments for the daemon command. """
    parser = parent.add_parser('daemonall',
                               parents=[base_parser()],
                               help='daemon mode; automatically syncs your credstash table with your entire cluster. Requires DynamoDB Streams to be enabled for your table. '
                                    'Implies -f --force.')
    parser.add_argument('table',
                        action='store',
                        type=str,
                        help='Credstash table you want to pull values from')
    parser.add_argument('-c', '--context',
                        dest='context',
                        action='store',
                        type=str,
                        default=None,
                        help='kubernetes context (ignored if proxy is set)')
    parser.add_argument('-r', '--region',
                        dest='region',
                        action='store',
                        type=str,
                        default=None,
                        help='aws region')
    parser.add_argument('-i', '--interval',
                        dest='interval',
                        action='store',
                        type=int,
                        default=10,
                        help='how long to sleep between shard iterations (seconds)')
    return parser


def parse_args():
    """ Parses command line arguments. """
    # https://docs.python.org/3/library/argparse.html
    help_text = 'push a Credstash table to a Kubernetes secret'

    parser = argparse.ArgumentParser(description=help_text)

    parsers = parser.add_subparsers(dest='cmd')
    parsers.required = True

    add_parser_inject(parsers)
    add_parser_push(parsers)
    add_parser_pushall(parsers)
    add_parser_daemon(parsers)
    add_parser_daemonall(parsers)

    args = parser.parse_args()

    return args


def credstash_getall(args):
    """ Returns an object containing all your Credstash secrets from `args.table`. """
    # https://github.com/fugue/credstash/blob/master/credstash.py#L297
    if args.verbose:
        print('fetching your secrets from "{table}" '
              '(Credstash is slow, this may take a few minutes...)'.format(table=args.table))
    session_params = credstash.get_session_params(None, None)
    secrets = credstash.getAllSecrets('',
                                      region=args.region,
                                      table=args.table,
                                      **session_params)
    return secrets


def credstash_getone(name, args):
    """ Returns one single secret from from credstash table `args.table`. """
    if args.verbose:
        print('fetching your secret from "{table}" '.format(table=args.table))
    session_params = credstash.get_session_params(None, None)
    return credstash.getSecret(name,
                               region=args.region,
                               table=args.table,
                               **session_params)


def dns_subdomain(string):
    """
    Converts an ENV_VARIABLE style string to a secret-style string.
    This should be used to convert Credstash secret keys to Kubernetes secret keys.
    Explanation:
        Kubernetes will only let you store secrets if the key conforms to
        DNS_SUBDOMAIN. [1]
        rfc1035/rfc1123 subdomain (DNS_SUBDOMAIN): One or more lowercase
        rfc1035/rfc1123 labels separated by '.' with a maximum length of 253
        characters [2]
    [1] https://kubernetes.io/docs/concepts/configuration/secret/
    [2] https://github.com/kubernetes/community/blob/master/contributors/design-proposals/identifiers.md
    """
    return string.replace('_', '-').lower()


def generate_key(args, string):
    """Only convert to dns_subdomain if the --lowercase flag is set.
       Only convert to ENV_VAR if the --uppercase flag is set."""
    if args.uppercase:
        return reverse_dns_subdomain(string)
    elif args.lowercase:
        return dns_subdomain(string)
    return string


def reverse_dns_subdomain(string):
    """ The opposite of dns_subdomain, convert secret-style strings to ENV_VARIABLE style strings. """
    return string.replace('-', '_').upper()


def maybe_reverse_dns_subdomain(args, string):
    """Only convert from dns_subdomain if the --lowercase flag is set. """
    return reverse_dns_subdomain(string) if args.lowercase else string


def get_kube_client(args):
    """
    Return a kubernetes.client.CoreV1Api object
    with the default configuration set correctly
    by considering the proxy and the context variables
    """
    if args.in_cluster == False:
        kubernetes.config.load_kube_config()
    else:
        kubernetes.config.load_incluster_config()
    api_client = kubernetes.client.CoreV1Api()
    return api_client
    #api_client = kubernetes.client.ApiClient(configuration=kubernetes.client.configuration)
    #return kubernetes.client.CoreV1Api(api_client=api_client)


def kube_init_secret(args, name, data):
    """
    Initialize a Kubernetes secret object (only in memory).
    Data contains the secret data. Each key must consist of alphanumeric
    characters, '-', '_' or '.'. The serialized form of the secret data
    is a base64 encoded string, representing the arbitrary
    (possibly non-string) data value here.
    [1] https://github.com/kubernetes-incubator/client-python/blob/master/kubernetes/docs/V1Secret.md
    """
    # https://github.com/kubernetes-incubator/client-python/blob/master/kubernetes/docs/V1Secret.md
    # api_version, data, kind, metadata, string_data, type
    converted_data = {
        generate_key(args, key): base64.b64encode(data[key].encode('utf-8')).decode('utf-8')
        for key in data
    }
    metadata = kubernetes.client.V1ObjectMeta(name=name)
    return kubernetes.client.V1Secret(data=converted_data, type='Opaque', metadata=metadata)


def kube_create_secret(args, namespace, secret, data):
    """ Creates a Kubernetes secret. Returns the api response from Kubernetes."""
    # https://github.com/kubernetes-incubator/client-python/blob/master/kubernetes/docs/CoreV1Api.md#create_namespaced_secret
    kube = get_kube_client(args)
    body = kube_init_secret(args, secret, data)
    return kube.create_namespaced_secret(namespace, body)


def kube_replace_secret(args, namespace, secret, data):
    """ Replaces a kubernetes secret. Returns the api response from Kubernetes. """
    # https://github.com/kubernetes-incubator/client-python/blob/master/kubernetes/docs/CoreV1Api.md#replace_namespaced_secret
    kube = get_kube_client(args)
    body = kube_init_secret(args, secret, data)
    return kube.replace_namespaced_secret(secret, namespace, body)


def kube_secret_exists(args,namespace, secret):
    """ Returns True or False if a Kubernetes secret exists or not respectively. """
    # https://github.com/kubernetes-incubator/client-python/blob/master/kubernetes/docs/CoreV1Api.md#read_namespaced_secret
    kube = get_kube_client(args)
    try:
        # TODO: might be better to call list_namespaced_secrets here.
        kube.read_namespaced_secret(secret, namespace)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return False  # 404 means the secret did not exist, so we can return False
        else:
            raise  # don't catch errors you can't resolve.
    return True


def kube_namespace_exists(args):
    """ Returns True or False if a Kubernetes namespace exists or not respectively. """
    # https://github.com/kubernetes-incubator/client-python/blob/master/kubernetes/docs/CoreV1Api.md#read_namespaced_secret
    kube = get_kube_client(args)
    try:
        # TODO: might be better to call list_namespaced_secrets here.
        kube.read_namespace(args.namespace)
    except kubernetes.client.rest.ApiException as e:
        if e.status == 404:
            return False  # 404 means the secret did not exist, so we can return False
        else:
            raise  # don't catch errors you can't resolve.
    return True


def kube_read_secret(args):
    """ Returns the full contents of a Kubernetes secret. """
    kube = get_kube_client(args)
    return kube.read_namespaced_secret(args.secret, args.namespace)


def kube_read_deployment(args):
    """ Returns the full contents of Kubernetes deployment. """
    kube = get_kube_client(args)
    response = kube.read_namespaced_deployment(args.deployment, args.namespace)
    return response


def kube_patch_deployment(args, deployment):
    """ Patches a Kubernetes deployment with data `deployment`. Returns the full contents of the patched deployment. """
    kube = get_kube_client(args)
    return kube.patch_namespaced_deployment(args.deployment, args.namespace, deployment)


def init_env(name, secret_name, secret_key):
    """ Initialize a Kubernetes env PATCH structure (dict). """
    # see: https://kubernetes.io/docs/concepts/configuration/secret/#using-secrets-as-environment-variables
    # create a new env structure
    obj = {
        'name': name,
        'value': None,
        'valueFrom': {
            'secretKeyRef': {
                'name': secret_name,
                'key': secret_key
            }
        }
    }
    return obj


def init_envs_for_container(args, secrets, container):
    """
    Initialize a set of envs to PATCH to a container
    if `--update-only` is specified, we only return envs
    which are already present inside the container.
    """
    # initialize a list of envs to PATCH to kubernetes
    envs = [
        init_env(
            maybe_reverse_dns_subdomain(args, key),
            args.secret,
            key)
        for key in secrets
    ]
    if args.update_only:
        # compile a list of environment variable names in the container -
        # so we can easily check which envs are present
        container_env_names = [
            env.name
            for env in container.env
        ]
        # only return envs that are already present inside the container
        envs = [
            env
            for env in envs
            if env['name'] in container_env_names
        ]
    return envs


def cmd_inject(args):
    """
    Pulls values from a Kubernetes secret and injects them into a deployment as environment variables.
    There may be multiple containers in a single deployment, so we inject into all of them.
    """
    # read the original deployment file
    deployment = kube_read_deployment(args)
    # read the secrets
    secrets = kube_read_secret(args).data
    # TODO: implement a diff here so we can inform the use if they actually changed anything.
    # TODO: this is a bit too complex, find a way to simplify
    data = {
        'spec': {
            'template': {
                'spec': {
                    'containers': [
                        {
                            'name': container.name,
                            'env': init_envs_for_container(args, secrets, container)
                        }
                        for container in deployment.spec.template.spec.containers
                        # if no --container is passed in, we inject in every container
                        if (container.name in args.container) or (len(args.container) == 0)
                    ]
                }
            }
        }
    }
    kube_patch_deployment(args, data)
    print('inject is DEPRECATED; see README.md, use envFrom instead)\n\n'
          'Injected environment variables into deployment: "{deployment}" '
          'from secret: "{secret}"'.format(deployment=args.deployment, secret=args.secret))


def cmd_push(args):
    """ Pulls values from a Credstash table and stores them in a Kubernetes secret. """

    if args.verbose:
        print('checking that "{secret}" exists...'.format(secret=args.secret))
    data = credstash_getall(args)
    prefix = "{0}/{1}/secrets/{2}".format(args.namespace,args.environment, args.secret)
    secretData = {}
    for secret, value in data.items():
        if prefix in secret:
            secret = secret.strip(prefix)
            secretData[secret] = value
            
    if kube_secret_exists(args, args.namespace, args.secret):
        if not args.force:
            print('kubernetes Secret: "{secret}" already exists, run with -f to replace it.'.format(secret=args.secret))
            sys.exit(1)
        else:
            kube_replace_secret(args, args.namespace, args.secret, secretData)
            print('replaced Kubernetes Secret: "{secret}" with Credstash table: "{table}"'.format(secret=args.secret,
                                                                                                  table=args.table))
    else:
        kube_create_secret(args, args.namespace, args.secret, secretData)
        print('created Kubernetes Secret: "{secret}" with Credstash table: "{table}"'.format(table=args.table,
                                                                                             secret=args.secret))
def cmd_pushall(args):
    """Syncs a Credstash table with an entire cluster"""
    if not kube_namespace_exists(args):
    	print('kubernetes namespace: "{namespace}" doesn\'t exist. Please create it before running kubestash'.format(namespace=args.namespace))
        sys.exit(1)

    # Map of all namespaces to secrets
    secretMap = {}

    prefix = ''

    # This is incorrect, since it doesn't work for the `default` namespace
    # TODO: Figure out a way!
    if args.namespace != 'default':
    	prefix = "{0}/{1}/secrets/".format(args.namespace,args.environment)
        secrets = credstash_getall(args)
        secrets = {k: secrets[k] for k in secrets if k.startswith(prefix)}
	for key, value in secrets.items():
            if prefix in key and '/' in key:
                secret, secretname = key.strip(prefix).split('/')
                if secret not in secretMap:
                    secretMap[secret] = {secretname: value}
                else:
                    secretMap[secret][secretname] = value
        ns = args.namespace
        for secret, data in secretMap.items():
            if kube_secret_exists(args,args.namespace, secret):
                print("Force pushing secret to kubernetes: ns={ns}, secret={secret}".format(ns=ns, secret=secret))
                kube_replace_secret(args, args.namespace, secret, data)
            else:
                print("Creating and pushing secret to kubernetes: ns={ns}, secret={secret}".format(ns=ns, secret=secret))
                kube_create_secret(args, args.namespace, secret, data)
    if args.verbose:
        print("All secrets synced")
        

def get_stream_client(args):
    client = boto3.client('dynamodbstreams', region_name=args.region)

    response = client.list_streams(TableName=args.table, Limit=100)

    if not response['Streams']:
        print("fatal: no stream found for DynamoDB Table '{table}'.\n"
              "ensure streams are enabled for your table:\n"
              "\thttps://console.aws.amazon.com/dynamodb/home\n"
              .format(table=args.table))
        sys.exit(1)


    # take the first stream we find... not sure if there are any caveats in doing this.
    if len(response['Streams']) > 0:
        arn = response['Streams'][0]['StreamArn']

        working_shard_id = None
        start_sequence_number = None

        if args.verbose:
            print("using DynamoDB Stream ARN: {arn}".format(arn=arn))

        response = client.describe_stream(StreamArn=arn, Limit=100)

        if not response['StreamDescription']:
            print("fatal: no StreamDescription found for DynamoDB stream '{arn}'.".format(arn=arn))
            sys.exit(1)

        if not response['StreamDescription']['Shards']:
            print("fatal: no shards found for DynamoDB stream '{arn}'.".format(arn=arn))
            sys.exit(1)
        try:
            for shard in response['StreamDescription']['Shards']:
                seqNum = shard['SequenceNumberRange']
                if 'StartingSequenceNumber' in seqNum and 'EndingSequenceNumber' not in seqNum:
                    working_shard_id = shard['ShardId']
                    start_sequence_number = seqNum['StartingSequenceNumber']
                    break
            if args.verbose:
                print("Shard Id:{0}, Sequence Number:{1}".format(working_shard_id, start_sequence_number))
        except Exception as exc:
            print("fatal: error fetching shard id:{0}".format(exc))
            sys.exit(1)

        if args.verbose:
            print("using DynamoDB Stream shard id: {shard_id}".format(shard_id=working_shard_id))

        response = client.get_shard_iterator(StreamArn=arn, ShardId=working_shard_id,
                                             ShardIteratorType='AFTER_SEQUENCE_NUMBER',
                                             SequenceNumber=start_sequence_number)

        shard_iterator = response['ShardIterator']

        return (client, shard_iterator)


def cmd_daemon(args):
    # https://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html
    # https://boto3.readthedocs.io/en/latest/reference/services/dynamodbstreams.html
    # TODO: what if there are more than 100 streams?

    # -f --force is implied by daemon
    args.force = True

    client, shard_iterator = get_stream_client(args)

    cmd_push(args)

    if args.verbose:
        print("checking DynamoDB Stream for changes...")

    response = client.get_records(ShardIterator=shard_iterator, Limit=100)

    if len(response['Records']) > 0:
        print("detected DynamoDB changes, running push command...")
        cmd_push(args)

    time.sleep(args.interval)

    while True:
        shard_iterator = response['NextShardIterator']
        if args.verbose:
            print("checking DynamoDB Stream for changes...")
        response = client.get_records(ShardIterator=shard_iterator, Limit=100)
        print(len(response['Records']))
        if len(response['Records']) > 0:
            print("detected DynamoDB changes, running push command...")
            cmd_push(args)
        time.sleep(args.interval)


def cmd_daemonall(args):
    args.force = True
    args.secretname = None

    ## During startup do a force push of all the secrets to avoid dynamodb event loss
    try:
        argscopy = copy.copy(args)
        cmd_pushall(argscopy)
    except Exception as exc:
        print "Fatal: Error performing startup cmd_pushall:{0}".format(exc)
        sys.exit(1)


    client, shard_iterator = get_stream_client(args)

    if args.verbose:
        print("checking DynamoDB Stream for changes...")

    response = client.get_records(ShardIterator=shard_iterator, Limit=100)

    while True:
        shard_iterator = response['NextShardIterator']

        if args.verbose:
            print("checking DynamoDB Stream for changes...")

        response = client.get_records(ShardIterator=shard_iterator, Limit=100)

        if 'Records' not in response:
            print ("Error: No Records found in response for Shard:{0}".format(shard_iterator))

        else:
            records = response['Records']

            if args.verbose:
                print "Shard ID:{0}, Record Len:{1}".format(shard_iterator, len(records))

            if len(records) > 0:
                for record in records:
                    try:
                        key = record['dynamodb']['Keys']['name']['S']
                        print("detected DynamoDB changes, running push command...")
                        argscopy = copy.copy(args)
                        argscopy.secretname = key
                        cmd_pushall(argscopy)
                    except Exception as exc:
                        print "Fatal: Error performing cmd_pushall:{0}".format(exc)
                        sys.exit(1)

        response = client.get_records(ShardIterator=shard_iterator, Limit=100)

        time.sleep(args.interval)

def load_config_file(args, config_file):
    if args.verbose:
        print('loading kubernetes config at: "{config_file}"'.format(config_file=config_file))

    # override the host if the user passes in a --proxy
    if args.proxy and (len(args.proxy) == 1):
        config = kubernetes.client.Configuration()
        config.host = args.proxy[0]
        kubernetes.client.configuration = config
        kubernetes.client.configuration.verify_ssl = False

    else:
        contexts, _ = kubernetes.config.list_kube_config_contexts()
        context_names = [c['name'] for c in contexts]
        if args.context and args.context not in context_names:
            print("Kubernetes context '{context}' not found, must be one of: {context_list}"
                  .format(context=args.context,
                          context_list=', '.join(context_names)))
            sys.exit(1)
        kubernetes.config.load_kube_config(config_file=config_file, context=args.context)

def main():
    args = parse_args()

    config_file = os.path.expanduser(os.environ.get('KUBECONFIG', '~/.kube/config'))

    if args.in_cluster == False:
        load_config_file(args, config_file)

    try:
        if args.cmd == 'push':
            cmd_push(args)
        elif args.cmd == 'pushall':
            cmd_pushall(args)
        elif args.cmd == 'inject':
            cmd_inject(args)
        elif args.cmd == 'daemon':
            cmd_daemon(args)
        elif args.cmd == 'daemonall':
            cmd_daemonall(args)
        else:
            pass
    except urllib3.exceptions.MaxRetryError as e:
        if (type(e.reason) is urllib3.exceptions.SSLError) and not args.trace:
            # This will be a very common error since the python that ships with macOS
            # seems to be stuck on openssl v0.9.8, so lets show the users how to fix it.
            # Kubernetes seems to be aware of this issue:
            # https://github.com/kubernetes-incubator/client-python#sslerror-on-macos
            #
            print(('\nSSLError: run with --trace to see the original exception which caused this error.\n\n'
                   'This version of python is compiled with "{ssl_version}" - '
                   'while Kubernetes requires at least version 1.0.0!\n\n'
                   'You can fix this by running:\n\n'
                   '\tkubectl proxy -p 8080\n\n'
                   '\tkubestash --proxy 127.0.0.1:8080 {table} {secret}\n\n'
                   'Which will subvert the issue by connecting to Kubernetes through an http proxy.\n\n'
                   'Alternatively, you can upgrade your openssl and rebuild python3 with brew.\n\n'
                   '\tbrew update\n'
                   '\tbrew install openssl\n'
                   '\tbrew uninstall python3\n'
                   '\tbrew install python3 --with-brewed-openssl\n'
                   ).format(ssl_version=ssl.OPENSSL_VERSION,
                            table=args.table,
                            secret=args.secret))
            sys.exit(1)
        elif (type(e.reason) is urllib3.exceptions.NewConnectionError) and not args.trace:
            print(('\nNewConnectionError: run with --trace to see the original exception which caused this error\n\n'
                   'Failed to connect to "{host}".\n\n'
                   '- is env KUBECONFIG set to the correct value "{config_file}" ?\n\n'
                   '- is your cluster.server (in "{config_file}") set to the right host ?\n\n'
                   '- is your apiserver reachable ?\n\n'
                   'use --proxy HOST to override the host if neccesary\n'
                   ).format(host=kubernetes.client.configuration.host,
                            config_file=config_file))
            sys.exit(1)
        else:
            raise

    return None
