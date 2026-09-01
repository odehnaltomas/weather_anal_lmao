from chmi_downloader.cli import print_collection_progress


def test_collection_progress_prints_download_details(capsys):
    print_collection_progress({
        'event': 'result',
        'product': 'echotop',
        'index': 1,
        'total': 2,
        'result': {
            'status': 'downloaded',
            'path': 'F:/weather_data/file.hdf',
            'size_bytes': 123,
        },
    })

    output = capsys.readouterr().out
    assert '[echotop] 1/2 Downloaded file.hdf (123 bytes).' in output
