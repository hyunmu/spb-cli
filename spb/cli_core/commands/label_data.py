import os
import click
import json
import logging
import rich
import rich.table
import rich.console
import rich.logging
import math
from multiprocessing import Process, Manager, Pool
import tqdm
import requests
from collections import ChainMap

import spb
from spb.cli_core.utils import recursive_glob_image_files, recursive_glob_label_files
from spb.models.label import Label

console = rich.console.Console()
logger = logging.getLogger()
simple_logger = logging.getLogger('simple')
NUM_MULTI_PROCESS = 4
LABEL_DESCRIBE_PAGE_SIZE = 10


class LabelData():

    def upload_data(self, project, dataset_name, directory_path, include_label, is_forced):
        imgs_path = recursive_glob_image_files(directory_path)
        if not is_forced:
            if not click.confirm(f"Uploading {len(imgs_path)} data and {len(recursive_glob_label_files(directory_path)) if include_label else 0 } labels to dataset '{dataset_name}' under project '{project.name}'. Proceed?"):
                    return
        asset_images = []
        manager = Manager()
        if len(imgs_path) != 0:
            for key in imgs_path:
                file_name = key
                asset_image = {
                    'file': imgs_path[key],
                    'file_name': file_name,
                    'data_key': key,
                    'dataset': dataset_name
                }
                asset_images.append(asset_image)
            data_results = manager.list([manager.dict()]*len(asset_images))
            console.print(f"Uploading data:")
            with Pool(NUM_MULTI_PROCESS) as p:
                list(tqdm.tqdm(p.imap(_upload_asset, zip([project.id] * len(asset_images), asset_images, data_results)), total=len(asset_images)))
        else:
            data_results = [{}]

        label_results = None
        if include_label:
            labels_path = recursive_glob_label_files(directory_path)
            console.print(f"Uploading labels:")
            if len(labels_path) != 0:
                label_results = manager.list([manager.dict()]*len(labels_path))
                with Pool(NUM_MULTI_PROCESS) as p:
                    list(tqdm.tqdm(p.imap(_update_label, zip(labels_path, [project.id]*len(labels_path), [dataset_name]*len(labels_path), label_results)), total=len(labels_path)))
            else:
                label_results = [{}]

        console.print('\n[b blue]** Result Summary **[/b blue]')
        success_data_count = len(asset_images) - len(data_results[0])
        data_success_ratio = round(success_data_count/len(asset_images)*100,2) if len(data_results[0]) != 0 else 100
        console.print(f'Successful upload of {success_data_count} out of {len(asset_images)} data. ({data_success_ratio}%) - [b red]{len(data_results[0])} ERRORS[/b red]')

        if include_label:
            success_label_count=len(labels_path)-len(label_results[0])
            label_success_ratio = round(success_label_count/len(labels_path)*100,2) if len(label_results[0]) != 0 else 100
            console.print(f'Successful upload of {success_label_count} out of {len(labels_path)} labels. ({label_success_ratio}%) - [b red]{len(label_results[0])} ERRORS[/b red]')
            self._print_error_table(dict(data_results[0]), dict(label_results[0]))
        else:
            self._print_error_table(data_results=dict(data_results[0]))

    def upload_label(self, project, dataset_name, directory_path, is_forced):
        labels_path = recursive_glob_label_files(directory_path)
        if not is_forced:
            if not click.confirm(f"Uploading {len(labels_path)} labels to project '{project.name}'. Proceed?"):
                return
        if len(labels_path) != 0:
            manager = Manager()
            label_results = manager.list([manager.dict()]*len(labels_path))
            with Pool(NUM_MULTI_PROCESS) as p:
                list(tqdm.tqdm(p.imap(_update_label, zip(labels_path, [project.id]*len(labels_path), [dataset_name]*len(labels_path), label_results)), total=len(labels_path)))
        else:
            label_results = [{}]

        console.print('\n[b blue]** Result Summary **[/b blue]')
        success_label_count=len(labels_path)-len(label_results[0])
        success_label_ratio = round(success_label_count/len(labels_path)*100,2) if len(labels_path) != 0 else 100
        console.print(f'Successful upload of {success_label_count} out of {len(labels_path)} labels. ({success_label_ratio}%) - [b red]{len(label_results[0])} ERRORS[/b red]')
        self._print_error_table(label_results=dict(label_results[0]))
        
        
    def _count_labels(self, project_id: str) -> int:
        result = 0 
        try:
            _, result = spb.run(command=spb.Command(type='describe_label'), 
                           option={
                                'project_id' : project_id
                           }, 
                           page_size=1, 
                           page=1)
        except Exception as e:
            logger.warning('Fail to describe_label', exec_info=True)
            result = 0
        return result

    
    def download(self, project, directory_path, is_forced):
        label_count = self._count_labels(project.id)
        if label_count != 0:
            page_length = int(label_count/LABEL_DESCRIBE_PAGE_SIZE) if label_count % LABEL_DESCRIBE_PAGE_SIZE == 0 else int(label_count/LABEL_DESCRIBE_PAGE_SIZE)+1
            if not is_forced:
                if not click.confirm(f"Downloading {label_count} data and {label_count} labels from project '{project.name}' to '{directory_path}'. Proceed?"):
                    return
            manager = Manager()
            results = manager.list([manager.dict()]*page_length)
            with Pool(NUM_MULTI_PROCESS) as p:
                list(tqdm.tqdm(p.imap(_download_worker, zip([project.id] * page_length, range(page_length), [directory_path] * page_length, results)), total=page_length))

            results = results[0]
            data_results = {}
            label_results = {}
            if len(results) > 0:
                for key in results.keys():
                    if 'data' in results[key]:
                        data_results[key] = results[key]['data']
                    if 'label' in results[key]:
                        label_results[key] = results[key]['label']
        else:
            label_results = {}
            data_results = {}

        console.print('\n[b blue]** Result Summary **[/b blue]')
        label_success_count = label_count - len(label_results)
        console.print(f'Successful download of {label_success_count} out of {label_count} labels. ({round(label_success_count/label_count*100,2)}%) - [b red]{len(label_results)} ERRORS[/b red]')
        data_success_count = label_count - len(data_results)
        console.print(f'Successful download of {data_success_count} out of {label_count} data. ({round(data_success_count/label_count*100,2)}%) - [b red]{len(data_results)} ERRORS[/b red]')

        self._print_error_table(label_results=label_results, data_results=data_results)

    def _print_error_table(self, data_results = None, label_results = None):
        results = {}

        if isinstance(data_results, dict):
            for key in data_results:
                results[key] = {}
                results[key]['data'] = data_results[key]
                results[key]['label'] = None
        if isinstance(label_results, dict):
            for key in label_results:
                if key in results:
                    results[key]['label'] = label_results[key]
                else:
                    results[key] = {'label':label_results[key], 'data':None}

        if not next(iter(results), None):
                return
        console.print('\n[b red]** Error Table **[/b red]')
        page = 1
        page_length = math.ceil(len(results)/10)
        while True:
            table = rich.table.Table(show_header=True, header_style="bold magenta")
            table.add_column("FILE NAME")
            if isinstance(data_results, dict):
                table.add_column("DATA UPLOAD")
            if isinstance(label_results, dict):
                table.add_column("LABEL UPLOAD")

            for _ in range(10):
                key = next(iter(results), None)
                if not key:
                    break
                if isinstance(data_results, dict) and isinstance(label_results, dict):
                    data = results[key]['data']
                    label = results[key]['label']
                    table.add_row(key, f"{data if data else '-'}", f"{label if label else '-'}")
                elif isinstance(data_results, dict):
                    data = results[key]['data']
                    table.add_row(key, f"{data if data else '-'}")
                else:
                    label = results[key]['label']
                    table.add_row(key, f"{label if label else '-'}")
                del results[key]
            console.print(table)
            if not next(iter(results), None):
                break
            else:
                click.echo(f'Press any button to continue to the next page ({page}/{page_length}). Otherwise press ‘Q’ to quit.', nl=False)
                key = click.getchar()
                click.echo()
                if key=='q' or key=='Q':
                    break
        console.log(f'[b]Check the log file for more details[/b]')
        console.log(f'- {simple_logger.handlers[0].baseFilename}')
        console.log(f'- {logger.handlers[0].baseFilename}')



def _download_worker(args):
    [project_id, page_idx, directory_path, result] = args
    command = spb.Command(type='describe_label')
    labels, _ = spb.run(command=command, option={
        'project_id' : project_id
    }, page_size = LABEL_DESCRIBE_PAGE_SIZE, page = page_idx + 1)
    for label in labels:
        error = {}
        path = os.path.join(label.dataset, label.data_key[1:]) if label.data_key.startswith('/') else os.path.join(label.dataset, label.data_key)
        path = os.path.join(directory_path, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        label_error = None
        data_error = None
        try:
            label_json_path = f'{path}.json'
            open(label_json_path, 'w').write(label.toJson())
        except Exception as e:
            error = {'label':str(e)}
            label_error = error
        try:
            data_url = label.data_url
            path = f'{path}'
            r = requests.get(data_url, allow_redirects=True)
            open(path, 'wb').write(r.content)
        except Exception as e:
            error.update({'data':str(e)})
            data_error = error

        if len(error) > 0:
            result[f'{label.dataset}/{label.data_key}'] = error
            if label_error:
                _ = dict()
                _set_error_result(f'{label.dataset}/{label.data_key}', _, str(label_error), label_error)
            if data_error:
                _ = dict()
                _set_error_result(f'{label.dataset}/{label.data_key}', _, str(data_error), data_error)


def _upload_asset(args):
    logging.debug(f'Uploading Asset: {args}')

    [project_id, asset_image, result] = args
    try:
        command = spb.Command(type='create_data')
        spb.run(command=command, option=asset_image, optional={'projectId': project_id})
    except Exception as e:
        _set_error_result(asset_image['data_key'], result, str(e), e)
        pass


def _update_label(args):
    [label_path, project_id, dataset, result] = args
    data_key = ".".join(label_path.split(".")[:-1])
    if not os.path.isfile(label_path):
        _set_error_result(data_key, result, 'Label json file is not existed.')
        return

    option = {
        'project_id': project_id,
        'dataset': dataset,
        'data_key': data_key
    }
    try:
        command = spb.Command(type='describe_label')
        described_labels, _ = spb.run(command=command, option=option, page_size=1, page=1)
        described_label = described_labels[0] if described_labels and described_labels[0] else None
        if described_label is None:
            _set_error_result(data_key, result, 'Label cannot be described.')
            return
        if described_label.data_key != option['data_key'] and described_label.dataset != option['dataset']:
            _set_error_result(data_key, result, 'Described label does not match to upload.')
            return
    except Exception as e:
        _set_error_result(data_key, result, str(e), e)
        return
    
    label = {
        "id": described_label.id,
        "project_id": project_id,
        "tags": [tag.get_datas(tag) for tag in described_label.tags],
        "result": described_label.result,
    }
    try:
        with open(label_path) as json_file:
            json_data = json.load(json_file)
        if json_data['result'] is None:
            return

        if 'result' in json_data:
            label['result'] = json_data['result']
        if 'tags' in json_data:
            label['tags'] = json_data['tags']
        command = spb.Command(type='update_label')
        label = spb.run(command=command, option=label)
        with open(label_path, 'w') as f:
            f.write(label.toJson())
    except Exception as e:
        _set_error_result(data_key, result, str(e), e)

def _set_error_result(key, result, message, exception=None):
    result[key] = message
    simple_logger.error(f'{key}    {message}', exc_info=True)
#     if exception:
#         logger.error(f'{key}    {message}')
#         logger.error(exception, exc_info=True)
#     else:
#         logger.error(f'{key}    {message}')
