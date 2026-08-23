import datetime

from fenetre.sun_path_svg import create_sun_path_svg, overlay_time_bar


def test_create_sun_path_svg_handles_alaska_twilight_without_dawn_or_dusk():
    svg = create_sun_path_svg(
        date=datetime.date(2026, 6, 10),
        latitude=64.50186,
        longitude=-165.4128,
        timezone="America/Los_Angeles",
    )

    assert svg.count("<path ") == 2
    assert "M 0.00,45" in svg
    assert "M 228.95,45" in svg
    assert "1000.00,45" in svg
    assert 'x2="250.00"' in svg


def test_overlay_time_bar_still_marks_alaska_sun_path():
    svg = create_sun_path_svg(
        date=datetime.date(2026, 6, 10),
        latitude=64.50186,
        longitude=-165.4128,
        timezone="America/Los_Angeles",
    )
    marked_svg = overlay_time_bar(
        svg,
        datetime.datetime(2026, 6, 10, 23, 11, 20),
        overlay_rect_width=4,
    )

    assert 'rect x="964.20"' in marked_svg
