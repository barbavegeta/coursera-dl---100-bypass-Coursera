"""
This module contains implementation for extractors. Extractors know how
to parse site of MOOC platform and return a list of modules to download.
Usually they do not download heavy content, except when necessary
to parse course syllabus.
"""

import abc
import json
import logging

from .api import CourseraOnDemand
from .api import ModulesV1, LessonsV1, ItemsV2
from .define import OPENCOURSE_ONDEMAND_COURSE_MATERIALS_V2
from .network import get_page
from .utils import is_debug_run, spit_json


class PlatformExtractor(object):
    __metaclass__ = abc.ABCMeta

    def get_modules(self):
        """
        Get course modules.
        """
        pass


class CourseraExtractor(PlatformExtractor):
    def __init__(self, session):
        self._notebook_downloaded = False
        self._session = session

    def list_courses(self):
        """
        List enrolled courses.

        @return: List of enrolled courses.
        @rtype: [str]
        """
        course = CourseraOnDemand(session=self._session,
                                  course_id=None,
                                  course_name=None)
        return course.list_courses()

    def get_modules(self, class_name,
                    reverse=False, unrestricted_filenames=False,
                    subtitle_language='en', video_resolution=None,
                    download_quizzes=False, mathjax_cdn_url=None,
                    download_notebooks=False):

        page = self._get_on_demand_syllabus(class_name)
        error_occurred, modules = self._parse_on_demand_syllabus(
            class_name,
            page, reverse, unrestricted_filenames,
            subtitle_language, video_resolution,
            download_quizzes, mathjax_cdn_url, download_notebooks)

        return error_occurred, modules

    def _get_on_demand_syllabus(self, class_name):
        """
        Get the on-demand course listing webpage.
        """
        url = OPENCOURSE_ONDEMAND_COURSE_MATERIALS_V2.format(
            class_name=class_name)
        page = get_page(self._session, url)
        logging.debug('Downloaded %s (%d bytes)', url, len(page))

        return page

    def _parse_on_demand_syllabus(self, course_name, page, reverse=False,
                                  unrestricted_filenames=False,
                                  subtitle_language='en',
                                  video_resolution=None,
                                  download_quizzes=False,
                                  mathjax_cdn_url=None,
                                  download_notebooks=False):
        """
        Parse a Coursera on-demand course listing/syllabus page.

        @return: Tuple of (bool, list), where bool indicates whether
            there was at least on error while parsing syllabus, the list
            is a list of parsed modules.
        @rtype: (bool, list)
        """

        dom = json.loads(page)
        class_id = dom['elements'][0]['id']

        logging.info('Parsing syllabus of on-demand course (id=%s). '
                     'This may take some time, please be patient ...',
                     class_id)
        modules = []

        # Use v2 JSON data exclusively; skip the old v1 endpoint
        json_modules = dom['linked'].get('onDemandCourseMaterialItems.v2', [])
        course = CourseraOnDemand(
            session=self._session, course_id=class_id,
            course_name=course_name,
            unrestricted_filenames=unrestricted_filenames,
            mathjax_cdn_url=mathjax_cdn_url)
        course.obtain_user_id()

        if is_debug_run():
            spit_json(dom, f'{course_name}-syllabus-raw.json')
            spit_json(json_modules, f'{course_name}-material-items-v2.json')

        # Parse modules and items from v2 only
        all_modules = ModulesV1.from_json(
            dom['linked'].get('onDemandCourseMaterialModules.v1', []))
        all_lessons = LessonsV1.from_json(
            dom['linked'].get('onDemandCourseMaterialLessons.v1', []))
        all_items = ItemsV2.from_json(
            json_modules)

        error_occurred = False
        for module in all_modules:
            logging.info('Processing module  %s', module.slug)
            lessons = []
            for section in module.children(all_lessons):
                logging.info('Processing section     %s', section.slug)
                lectures = []
                available_lectures = section.children(all_items)

                for lecture in available_lectures:
                    typename = lecture.type_name
                    logging.info('Processing lecture         %s (%s)',
                                 lecture.slug, typename)

                    links = {}
                    if typename == 'lecture':
                        links = course.extract_links_from_lecture(
                            class_id, lecture.id,
                            subtitle_language, video_resolution)
                    elif typename == 'supplement':
                        links = course.extract_links_from_supplement(
                            lecture.id)
                    elif typename in ('gradedProgramming', 'ungradedProgramming'):
                        links = course.extract_links_from_programming(
                            lecture.id)
                    elif typename == 'quiz' and download_quizzes:
                        links = course.extract_links_from_quiz(
                            lecture.id)
                    elif typename == 'exam' and download_quizzes:
                        links = course.extract_links_from_exam(
                            lecture.id)
                    elif typename == 'notebook' and download_notebooks:
                        logging.warning(
                            'According to notebooks platform, content will be downloaded first')
                        links = course.extract_links_from_notebook(
                            lecture.id)
                    else:
                        continue

                    if links is None:
                        error_occurred = True
                    elif links:
                        lectures.append((lecture.slug, links))

                if lectures:
                    lessons.append((section.slug, lectures))

            if lessons:
                modules.append((module.slug, lessons))

        if modules and reverse:
            modules.reverse()

        # Processing resources section
        json_references = course.extract_references_poll()
        if json_references:
            logging.info('Processing resources')
            references = []
            for json_reference in json_references:
                links = course.extract_links_from_reference(
                    json_reference['shortId'])
                if links:
                    references.append((json_reference['slug'], [('', links)]))
            if references:
                modules.append(('Resources', references))

        return error_occurred, modules
