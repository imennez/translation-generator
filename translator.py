import argparse
import os
import sys
import re
import yaml
import json
import jinja2
from collections import defaultdict

program = os.path.basename(sys.argv[0])
config_file = 'translation-config.yml'

class Driver:
    def main(self, args=sys.argv[1:], prog=program):
        options = self.parse_args(args, prog)
        generate = options.generate
        data = self.load_config()
        Validator().validate(data)
        all_bundles = Bundler().gather(data)
        TranslationGenerator(options, all_bundles).generate_all()

    def parse_args(self, args, prog):
        parser = argparse.ArgumentParser(
            prog=prog,
            description='Generator for candidate translation strings',
        )

        parser.add_argument('--output',
                            help='output type',
                            choices=("yaml", "json"),
                            default="yaml")

        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument('--generate',
                          help='generate lock file',
                          action='store_true', default=False)
        return parser.parse_args(args)

    def load_config(self):
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                data = yaml.full_load(f)
        return data

class JsonParser(object):
    '''
        Parser that converts a list of json files into a dictionary representation
        where the key is the filename itself and the value is the json parsing of the file
    '''
    files = []
    dict_representation = {}

    def __init__(self, files):
        self.files = set(files)
        self.parse_to_dict(files)

    def parse_to_dict(self, files):
        for file in files:
            with open(file) as f:
                dictdump = json.loads(f.read())
            self.dict_representation[file] = dictdump

    def get_as_dictionary(self):
        return self.dict_representation

class PropertiesParser(object):
    '''
        Parser that converts a list of properties files into a dictionary representation
        where the key is the filename itself and the value is the (key, value) pair representing
        each entry in the properties file
    '''
    files = []
    separator = '='
    comment_char='#'
    dict_representation = {}

    def __init__(self, files):
        self.files = set(files)
        self.parse_to_dict(self.files)

    def parse_to_dict(self, files):
        for file in files:
            current_file_key_val = {}
            with open(file, "rt") as f:
                for line in f:
                    l = line.strip()
                    if l and not l.startswith(self.comment_char):
                        key_value_split = l.split(self.separator)
                        key = key_value_split[0].strip()
                        value = self.separator.join(key_value_split[1:]).strip().strip('"')
                        if value:
                            current_file_key_val[key] = value
            self.dict_representation[file] = current_file_key_val

    def get_as_dictionary(self):
        return self.dict_representation

class Bundle(object):
    whoami = __qualname__

    def __init__(self, path, extension, files, default_locale=None):
        self.path = path
        self.extension = extension
        self.files = set(files)
        self.default_locale = default_locale or "en_US"

    def get_default_locale_file(self):
        locale_regex_match = []
        for file in self.files:
            '''
                This regex matches all files that have _{default_locale} in their
                filename but not if that file also contains the word snapshot. This
                is to prevent the regex matching of the snapshot file when looking
                for the default locale file
            '''
            if re.match(rf'.*_{self.default_locale}(?!(.*snapshot)).*', file):
                locale_regex_match.append(file)
        if len(locale_regex_match) > 1:
            sys.exit(
                f'{self.whoami}: There were multiple regex matches of '
                + f'_{self.default_locale} in {self.path}: {locale_regex_match}. '
                + f'Expecting only a single match'
            )
        elif len(locale_regex_match) == 0:
            sys.exit(
                f'{self.whoami}: Expected default locale file to match regex '
                + f'.*_{self.default_locale} but no files in {self.path} matched'
            )
        else:
            return locale_regex_match[0]

    def generate_snapshot_file(self):
        default_locale_path = self.get_default_locale_file()
        snapshot_file_path = ''.join((default_locale_path, '.snapshot'))
        if not os.path.exists(snapshot_file_path):
            print(
                f'generating snapshot file {snapshot_file_path} '
                + f'based on {default_locale_path}'
            )
            with open(snapshot_file_path, 'a') as snap, open(default_locale_path, 'r') as default:
                for line in default:
                    snap.write(line)
        if snapshot_file_path not in self.files:
            self.files.add(snapshot_file_path)
        return snapshot_file_path

class Bundler:
    all_bundles = []

    def parse_bundle(self, bundle):
        path = bundle.get('path')
        extension = bundle.get('extension')
        default_locale = bundle.get('default_locale')
        resolved_path = Utilities().resolve_path(path)
        all_files_in_bundle_path = []
        for file in os.listdir(resolved_path):
            if file.endswith(extension):
                file_path = os.path.join(resolved_path, file)
                all_files_in_bundle_path.append(file_path)
        bundle_object = Bundle(path=resolved_path, extension=extension, files=all_files_in_bundle_path, default_locale=default_locale)
        self.all_bundles.append(bundle_object)

    def gather(self, data):
        for bundle in data.get('bundles'):
            self.parse_bundle(bundle)
        for bundle_obj in self.all_bundles:
            bundle_obj.generate_snapshot_file()
        return self.all_bundles

class TranslationGenerator:
    '''
        This class will accept a list of Bundle objects and apply the necessary parser
        to generate all the differences between the default_locale, it's corresponding
        snapshot and all the other locales
    '''
    whoami = __qualname__
    all_bundles = []
    additions = {}
    missing = {}

    def __init__(self, options, all_bundles):
        self.options = options
        self.all_bundles = all_bundles

    def check_missing_keys_in_other_locales(self, source, bundle):
        source_dict = bundle[source]
        for file in bundle.keys():
            missing_keys = {}
            candidate_dict = bundle[file]
            missing_keys = [f'{key}: {val}' for key, val in source_dict.items() if key not in candidate_dict.keys()]
            if missing_keys:
                self.missing[file] = missing_keys

    def new_entries(self, source, candidate, bundle):
        source_dict = bundle[source]
        candidate_dict = bundle[candidate]
        if source_dict != candidate_dict:
            new_values = [candidate_dict[x] for x in candidate_dict.keys() if candidate_dict[x] not in source_dict.values()]
            '''
                TODO: Ignoring the capability to actually make an inplace
                edit on the default locale file for any key value pair.
                With the logic currently implemented, this will show up
                simply as a "new addition" and the snapshot file will
                become stale with the old key that was actually "removed"
                in the default locale file. How do we reconcile this?
            '''
            if new_values:
                self.additions[candidate] = new_values

    def process_bundle(self, bundle):
        parsed_bundle = None
        if bundle.extension == 'json':
            parsed_bundle = JsonParser(bundle.files).get_as_dictionary()
        elif bundle.extension == 'properties':
            parsed_bundle = PropertiesParser(bundle.files).get_as_dictionary()
        else:
            raise(f'{whoami}: Unsupported bundle extension {bundle.extension}')
        snapshot_file = bundle.generate_snapshot_file()
        default_locale = bundle.get_default_locale_file()
        self.new_entries(source=snapshot_file, candidate=default_locale, bundle=parsed_bundle)
        self.check_missing_keys_in_other_locales(source=snapshot_file, bundle=parsed_bundle)

    def generate_all(self):
        for bundle in self.all_bundles:
            self.process_bundle(bundle)
        Reconciliator(self.options).print_manifest(self.missing, self.additions)

class Reconciliator:
    data = {}

    def __init__(self, options):
        self.options = options

    def print_manifest(self, missing, added):
        for locale, added_strings in added.items():
            self.data["added"] = self.data.get("added") or []
            self.data["added"].append({locale: sorted(added_strings)})
        for locale, missed_strings in missing.items():
            self.data["missing"] = self.data.get("missing") or []
            self.data["missing"].append({locale: sorted(missed_strings)})

        if self.options.output == 'json' and self.data:
            print(json.dumps(self.data, indent=4))
        elif self.options.output == 'yaml' and self.data:
            print(yaml.dump(self.data))

class Validator:
    whoami = __qualname__
    def validate(self, data):
        ACCEPTED_FILE_TYPES = {'properties', 'json'}
        if data and 'bundles' in data and data.get('bundles'):
            for bundle in data.get('bundles'):
                self.validate_keys_in_bundle(bundle)
                path = Utilities().resolve_path(bundle.get('path'))
                if not os.path.exists(path):
                    sys.exit(
                        f'{self.whoami}: {path} does not exist'
                    )
                extension = bundle.get('extension')
                if extension not in ACCEPTED_FILE_TYPES:
                    sys.exit(
                        f'{self.whoami}: .{extension} files are not one of the supported types\n' +
                        ', '.join(sorted(ACCEPTED_FILE_TYPES))
                    )
                for fname in os.listdir(path):
                    if fname.endswith(extension):
                        break
                else:
                    sys.exit(f'{self.whoami}: no .{extension} file found in {path}')
        else:
            sys.exit(f'{self.whoami}: {config_file} does not have any bundles')

    def validate_keys_in_bundle(self, bundle):
       REQUIRED_KEYS = {'path', 'extension'}
       if REQUIRED_KEYS - set(bundle.keys()):
           sys.exit(
               f'{self.whoami}: bundle configuration must have keys ' +
                ', '.join(sorted(REQUIRED_KEYS)))

class Utilities:
    def resolve_path(self, path):
        return os.path.realpath(path)

if __name__ == '__main__':
    try:
        Driver().main()
    except KeyboardInterrupt:
        exit(130)
