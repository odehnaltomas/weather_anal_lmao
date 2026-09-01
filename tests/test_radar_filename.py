from pathlib import Path

from chmi_downloader.sources.radar import RadarResource, parse_radar_time


def test_radar_resource_discovers_products():
    resource = RadarResource()
    config = {
        'radar': {
            'products': {
                'maxz': {
                    'enabled': True,
                    'formats': ['hdf5'],
                    'url': 'https://example.com/maxz',
                }
            }
        }
    }
    discovered = resource.discover(config)
    assert len(discovered) == 1
    assert discovered[0]['product'] == 'maxz'
    assert discovered[0]['format'] == 'hdf5'
    assert discovered[0]['archive_all'] is True


def test_parse_radar_time_from_chmi_filename():
    parsed = parse_radar_time('T_PASV23_C_OKPR_20260630223000.hdf')

    assert parsed is not None
    assert parsed.isoformat() == '2026-06-30T22:30:00+00:00'
