define(["backbone", "config", "lib/leaflet", "jquery", "underscore", "refLocation",
        "leaflet/refMarker", "leaflet/proposalMarker", "collection/layers",
        "collection/regions", "leaflet/infoLayer", "appState", "utils"],
       function(B, config, L, $, _, refLocation, RefMarker,
                ProposalMarker, infoLayers, regions, info, appState, $u) {

           var zoomThreshold = 17;

           return B.View.extend({
               initialize: function() {
                   var state = appState.getState(),
                       mapOptions = {minZoom: 13,
                                     maxBounds: config.bounds,
                                     attributionControl: false,
                                     zoomControl: false},
                       lat = parseFloat(state.lat),
                       lng = parseFloat(state.lng),
                       zoom = parseInt(state.zoom),
                       bounds = null;

                   if (_.isFinite(lat) && _.isFinite(lng))
                       mapOptions.center = L.latLng(lat, lng);
                   else
                       mapOptions.center = config.refPointDefault;

                   if (_.isFinite(zoom))
                       mapOptions.zoom = zoom;
                   else
                       mapOptions.zoom = 14;

                   var map = L.map(this.el, mapOptions),
                       layer = L.tileLayer(config.tilesURL),
                       markersLayer = L.featureGroup(),
                       parcelLayer = L.geoJson();

                   this.map = map;

                   map.addLayer(layer);
                   map.addLayer(parcelLayer);
                   map.addLayer(markersLayer);

                   this.parcelLayer = parcelLayer;
                   this.markersLayer = markersLayer;

                   map.on("zoomend", _.bind(this.updateControls, this))
                       .on("moveend", _.bind(this.updateMarkers, this))
                       .on("popupopen", _.bind(this.onPopupOpened, this))
                       .on("moveend", function() {
                           var center = map.getCenter();
                           appState.extendHash({
                               lat: center.lat,
                               lng: center.lng,
                               zoom: map.getZoom()
                           }, true);
                       });

                   // Map from case numbers to L.Markers
                   this.caseMarker = {};

                   // Place the reference location marker:
                   this.placeReferenceMarker(refLocation);

                   // ... and subscribe to updates:
                   this.listenTo(this.collection, "add", this.proposalAdded)
                       .listenTo(this.collection, "remove", this.proposalRemoved)
                       .listenTo(this.collection, "change", this.changed)
                   // Reference location marker:
                       .listenTo(refLocation, "change", this.placeReferenceMarker)
                   // Informational overlays:
                       .listenTo(infoLayers, "change", this.layersChanged)
                   // Region base layers:
                       .listenTo(regions, "selectionLoaded", this.showRegions)
                       .listenTo(regions, "selectionRemoved", this.removeRegions)
                   // App behaviors:
                       .listenTo(appState, "shouldFocus", this.onFocused);

                   appState.onStateKeyChange("f.box", this.onBoxFilterChanged,
                                             this);

                   $(document).on("click", ".map-zoom-in",
                                  function() {
                                      map.zoomIn();
                                      return false;
                                  })
                       .on("click", ".map-zoom-out",
                           function() {
                               map.zoomOut();
                               return false;
                           });

                   return this;
               },

               getMap: function() {
                   return this.map;
               },

               selectionChanged: function(proposals, ids) {
                   var models = _.map(ids, proposals.get, proposals),
                       bounds = L.latLngBounds(
                           _.map(models,
                                 function(model) {
                                     return model.get("location");
                                 }));
                   this.map.setView(bounds.getCenter());
               },

               regionLayers: {},
               showRegions: function(regions, ids) {
                   this.map.setMaxBounds(null);

                   var self = this,
                       bounds = L.latLngBounds([]),
                       deferredBounds = $.Deferred(),
                       completeCount = 0;

                   _.each(ids, function(id) {
                       if (self.regionLayers[id]) {
                           var layer = self.regionLayers[id];
                           bounds.extend(layer.getBounds());

                           if (++completeCount == ids.length)
                               deferredBounds.resolve(bounds);

                           return;
                       }

                       var regionInfo = regions.get(id);
                       regionInfo.loadShape()
                           .done(function(shape) {
                               var layer = L.geoJson(shape,
                                                     {style: config.regionStyle});


                               layer.on("dblclick", function(e) {
                                   self.onDblClick(e);
                               });
                               bounds.extend(layer.getBounds());
                               self.regionLayers[id] = layer;
                               self.map.addLayer(layer);

                               if (++completeCount == ids.length)
                                   deferredBounds.resolve(bounds);
                           });
                       });

                   // Fit to visible regions?
                   deferredBounds.done(function(bounds) {
                       self.map.setMaxBounds(bounds.pad(1));

                       if (!bounds.contains(self.map.getBounds()))
                           self.map.fitBounds(bounds.pad(-1));

                       // HACK Do this here rather than inside the bounds
                       // collection because this is where we actually load the
                       // region geometries and therefore have access to the
                       // layers and their bounds methods.
                       regions._bounds = bounds;
                       regions.trigger("regionBounds", bounds);
                   });
               },

               removeRegions: function(_regions, ids) {
                   _.each(ids, function(id) {
                       var layer = this.regionLayers[id];

                       if (layer) {
                           this.map.removeLayer(layer);
                           delete this.regionLayers[id];
                       }
                   }, this);


                   // Refit?
               },

               // Layer ordering (bottom -> top):
               // tiles, base layers, parcel layer, permits layer

               // The Layer Group containing proposal markers, set during
               // initialization.
               markersLayer: null,

               // GeoJSON layer group containing parcel shapes
               parcelLayer: null,

               // Callbacks:
               proposalAdded: function(proposal) {
                   var loc = proposal.get("location");

                   if (!loc) return;

                   var z = this.map.getZoom() - zoomThreshold,
                       marker = new ProposalMarker(proposal),
                       proposals = this.collection;

                   marker.addTo(this.markersLayer);

                   this.caseMarker[proposal.get("caseNumber")] = marker;

                   marker
                       .on("mouseover", function(e) {
                           proposal.set({_hovered: true});
                       })
                       .on("mouseout", function(e) {
                           proposal.set({_hovered: false});
                       })
                       .on("click", function(e) {
                           proposals.setSelection(proposal.id);
                       })
                       .on("popupclose", function(e) {
                           proposals.removeFromSelection(proposal.id);
                       });


                   if (z >= 0 && this.map.getBounds().contains(marker.getLatLng())) {
                       marker.setZoomed(z);
                   }

                   this.listenTo(proposal, "change", this.changed);
               },

               proposalRemoved: function(proposal) {
                   // debugger;
                   var caseNumber = proposal.get("caseNumber"),
                       marker = this.caseMarker[caseNumber],
                       parcel = this.parcelLayers[caseNumber];
                   if (marker) {
                       this.markersLayer.removeLayer(marker);
                       delete this.caseMarker[caseNumber];
                   }
                   if (parcel)
                       this.parcelLayer.removeLayer(parcel);
               },

               // Map of case # -> ILayer objects
               parcelLayers: {},

               // Triggered when a child proposal changes
               changed: function(change) {
                   var self = this,
                       excluded = change.get("_excluded"),
                       caseNumber = change.get("caseNumber"),
                       marker= this.caseMarker[caseNumber];

                   if (marker) {
                       if (excluded) {
                           self.markersLayer.removeLayer(marker);
                       } else {
                           marker.addTo(self.markersLayer);
                       }
                   }

                   // Hide or show parcel outlines:
                   var parcelLayer = this.parcelLayers[change.get("caseNumber")];
                   if (change.get("_selected") || change.get("_hovered")) {
                       if (!parcelLayer) {
                           var parcel = change.get("parcel");

                           if (!parcel) return;

                           parcelLayer = L.GeoJSON.geometryToLayer(parcel);
                           parcelLayer.setStyle(config.parcelStyle);
                           this.parcelLayers[change.get("caseNumber")] = parcelLayer;
                       }

                       this.parcelLayer.addLayer(parcelLayer);
                   } else if (parcelLayer &&
                              (change.changed._selected === false ||
                               change.changed._hovered === false)) {
                       this.parcelLayer.removeLayer(parcelLayer);
                   }
               },

               onDblClick: function(e) {
                   refLocation.setFromLatLng(
                       e.latlng.lat,
                       e.latlng.lng);
               },

               /**
                * Triggered on the appState when the map should focus on a group
                * of models. It's a hack, but the pattern could be generalized.
                * Right now, appState is only designed to cope with state that
                * is persisted to the location hash.
                *
                * @param {Backbone.Model[]} models
                * @param {boolean} zoom
                */
               onFocused: function(models, zoom) {
                   var self = this,
                       ll = _.map(models, function(model) {
                           var loc = model.get("location");

                           return L.latLng(loc.lat, loc.lng);
                       });

                   if (models.length == 1) {
                       this.map.setView(ll[0], zoom ? zoomThreshold : this.map.getZoom());
                   } else {
                       var bounds = L.latLngBounds(ll);
                       this.map.fitBounds(bounds);
                   }
               },

               onPopupOpened: function(e) {
                   // Recenter the map view on the popup when it opens.
                   var pos = this.map.project(e.popup._latlng);
                   pos.y -= e.popup._container.clientHeight/2;
                   this.map.panTo(this.map.unproject(pos), {animate: true});
               },

               onBoxFilterChanged: function(box) {
                   if (box) {
                       var bounds = $u.boxStringToBounds(box);

                       if (this._filterBoundsRect)
                           this._filterBoundsRect.setBounds(bounds);
                       else
                           this._filterBoundsRect = L.rectangle(
                               bounds,
                               config.filterBoundsStyle)
                           .addTo(this.map);
                   } else if (this._filterBoundsRect) {
                       this.map.removeLayer(this._filterBoundsRect);
                       delete this._filterBoundsRect;
                   }
               },

               /* Getting information about the markers. */
               updateMarkers: function() {
                   var map = this.map,
                       pLayer = this.markersLayer,
                       bounds = map.getBounds(),
                       zoom = map.getZoom();

                   _.each(this.caseMarker, function(marker) {
                       var inBounds = bounds.contains(marker.getLatLng());

                       if (zoom >= zoomThreshold) {
                           if (!inBounds)
                               return;
                           marker.setZoomed(zoom - zoomThreshold);
                       } else {
                           marker.unsetZoomed();
                       }
                   });
               },

               updateControls: function() {
                   var zoom = this.map.getZoom(),
                       max = zoom >= this.map.getMaxZoom(),
                       min = !max && zoom <= this.map.getMinZoom();
                   $(".map-zoom-in").toggleClass("disabled", max);
                   $(".map-zoom-out").toggleClass("disabled", min);
               },

               // Store a reference to the reference marker
               _refMarker: null,
               placeReferenceMarker: function(refLocation) {
                   var loc = refLocation.getPoint();

                   // Don't show the ref marker if the user has not entered an
                   // address or clicked the geolocate button.
                   if (refLocation.get("setMethod") !== "auto") {
                       if (!this._refMarker) {
                           this._refMarker =
                               (new RefMarker(refLocation))
                               .addTo(this.markersLayer);
                       }

                       // Recenter
                       this.map.setView(loc, Math.max(this.map.getZoom(), 16),
                                        {animate: false});
                       this._refMarker.bringToBack();
                   } else if (this._refMarker) {
                       this.markersLayer.removeLayer(this._refMarker);
                       this._refMarker = null;
                   }
               },

               getBounds: function() {
                   return this.map.getBounds();
               },

               resetBounds: function() {
                   this.map.fitBounds(config.bounds);
               },

               zoomToRefLocation: function() {
                   var bounds = this._refMarker.getBounds();
                   this.map.fitBounds(bounds, {padding: [5, 5]});
               },

               _infoLayers: {},
               layersChanged: function(infoLayer) {
                   var id = infoLayer.get("id"),
                       color = infoLayer.get("color"),
                       layer = this._infoLayers[id];

                   if (infoLayer.changed.shown) {
                       if (layer) {
                           this.map.addLayer(layer);
                       } else {
                           var self = this;
                           infoLayer.getFeatures()
                               .done(function(features) {
                                   var layer = info.makeInfoLayer(infoLayer, features);
                                   self._infoLayers[id] = layer;
                                   self.map.addLayer(layer);

                               });
                       }

                       return;
                   }

                   if (!layer) return;

                   if (infoLayer.changed.color)
                       layer.style({color: color});

                   if (infoLayer.changed.shown === false)
                       this.map.removeLayer(layer);
               }
           });
       });
