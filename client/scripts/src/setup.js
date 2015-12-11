define(
    ["jquery", "permits", "permits-view", "map-view",
     "details-view", "minimap-view", "preview-view",
     "projects", "projects-view", "project-preview-view",
     "preview-manager", "tab-view", "layers-view",
     "glossary", "config", "backbone", "routes",
     "legal-notice"],
    function($, Permits, PermitsView, MapView, DetailsView,
             MinimapView, PreviewView, Projects, ProjectsView,
             ProjectPreview, PreviewManager, TabView, LayersView,
             glossary, config, B, routes) {
        return {
            start: function() {
                var proposals = new Permits(),
                    projects = new Projects();

                var showIntro = true;

                // Show introduction?
                $(document.body).toggleClass("main", !showIntro);
                if (showIntro) {

                }

                $(document).on("click", "#explore,#modal", function(e) {
                    $(document).trigger("showMain");
                });

                var mapView = new MapView({
                    collection: proposals,
                    el: "#map"
                });

                $(document).one("showMain", function() {
                    $(document.body).addClass("main");
                    new MinimapView({
                        el: "#minimap",
                        linkedMap: mapView.map
                    });
                });

                var detailsView = new DetailsView({
                    collection: proposals,
                    el: "#overlay"
                });

                var tabView = new TabView({
                    el: "#data",
                    subviews: {
                        "proposals": new PermitsView({
                            collection: proposals
                        }),
                        "projects": new ProjectsView({
                            collection: projects,
                            proposals: proposals
                        })
                    }
                });

                var proposalPreview = new PreviewView(),
                    projectPreview = new ProjectPreview(),
                    previewManager = new PreviewManager({
                        el: "#preview",
                        collections: {
                            proposals: proposals,
                            projects: projects
                        },
                        previewMap: {
                            projects: projectPreview,
                            proposals: proposalPreview
                        },
                        viewSelection: tabView
                    });

                var layers = new LayersView({
                    el: "#layers .contents"
                }).render();

                $(document)
                    .on("click", "a._collapse",
                        function() {
                            $(this).closest("._collapsible")
                                .addClass("collapsed")
                                .removeClass("expanded")
                                .trigger("collapsed");
                        })
                    .on("click", "a._expand",
                        function() {
                            $(this).closest("._collapsible")
                                .removeClass("collapsed")
                                .addClass("expanded")
                                .trigger("expanded");
                        });

                proposals.fetch({dataType: "jsonp"});
                projects.fetch({dataType: "jsonp"});

                // For testing:
                window.permits = proposals;

                glossary.init();

                return {
                    glossary: glossary,
                    map: mapView,
                    preview: previewManager,
                    details: detailsView,
                    exploreView: tabView,
                    layersView: layers
                };
            }
        };
    });
